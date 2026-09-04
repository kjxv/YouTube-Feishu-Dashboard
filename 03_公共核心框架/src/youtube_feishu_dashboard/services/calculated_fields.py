"""受控的标准字段表达式计算器。

这里只执行字段字典中的结构化表达式，绝不执行自然语言说明或任意 Python 代码。
复杂历史查询、状态迁移和外部调用继续由功能模块代码负责。
"""

from __future__ import annotations

import ast
import math
from dataclasses import dataclass
from typing import TypeAlias

from youtube_feishu_dashboard.catalog.field_catalog import FieldCatalog, FieldDefinition
from youtube_feishu_dashboard.core.errors import ConfigurationError

ExpressionValue: TypeAlias = str | int | float | bool | None

_FUNCTION_NAMES = frozenset(
    {"ABS", "CEIL", "COALESCE", "FLOOR", "IF", "MAX", "MIN", "ROUND", "SAFE_DIVIDE"}
)
_ALLOWED_NODE_TYPES = (
    ast.Expression,
    ast.Constant,
    ast.Name,
    ast.Load,
    ast.BinOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.UnaryOp,
    ast.UAdd,
    ast.USub,
    ast.Not,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.Compare,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.IfExp,
    ast.Call,
)
_MAX_EXPRESSION_LENGTH = 1000
_MAX_AST_NODES = 100


@dataclass(frozen=True, slots=True)
class CompiledCalculation:
    definition: FieldDefinition
    expression: ast.Expression


@dataclass(frozen=True, slots=True)
class CalculatedFieldEngine:
    """按依赖顺序计算一组安全表达式字段。"""

    calculations: tuple[CompiledCalculation, ...]
    required_api_field_ids: tuple[str, ...]
    required_module_field_ids: tuple[str, ...]

    @property
    def calculated_field_ids(self) -> tuple[str, ...]:
        return tuple(item.definition.standard_field_id for item in self.calculations)

    @classmethod
    def compile(
        cls,
        catalog: FieldCatalog,
        field_ids: tuple[str, ...] | list[str],
        *,
        module_code_field_ids: set[str] | frozenset[str] = frozenset(),
    ) -> CalculatedFieldEngine:
        requested = tuple(dict.fromkeys(field_ids))
        module_outputs = frozenset(module_code_field_ids)
        calculations: list[CompiledCalculation] = []
        api_inputs: list[str] = []
        module_inputs: list[str] = []
        visiting: list[str] = []
        visited: set[str] = set()

        def visit(field_id: str) -> None:
            if field_id in visited:
                return
            if field_id in visiting:
                start = visiting.index(field_id)
                cycle = visiting[start:] + [field_id]
                raise ConfigurationError("计算字段存在循环依赖：" + " -> ".join(cycle))

            catalog.validate_requirements((field_id,))
            definition = catalog.get(field_id)
            if definition.calculation_mode != "safe_expression":
                raise ConfigurationError(
                    f"{field_id} 不是安全表达式字段，不能交给通用计算引擎执行。"
                )
            if definition.implementation_status in {"planned", "deprecated"}:
                raise ConfigurationError(
                    f"{field_id} 的实现状态是 {definition.implementation_status}，暂不可执行。"
                )

            visiting.append(field_id)
            for dependency_id in definition.dependency_field_ids:
                catalog.validate_requirements((dependency_id,))
                dependency = catalog.get(dependency_id)
                if dependency.calculation_mode == "safe_expression":
                    visit(dependency_id)
                elif dependency.is_api_field:
                    _append_unique(api_inputs, dependency_id)
                elif dependency.calculation_mode == "module_code":
                    if dependency_id not in module_outputs:
                        raise ConfigurationError(
                            f"计算字段 {field_id} 依赖模块代码字段 {dependency_id}，"
                            "但当前模块没有在 output_field_ids 中登记该字段。"
                        )
                    _append_unique(module_inputs, dependency_id)
                else:
                    raise ConfigurationError(
                        f"计算字段 {field_id} 依赖 {dependency_id}，其计算方式是"
                        f" {dependency.calculation_mode}，不能作为程序计算输入。"
                    )

            expression = _compile_expression(definition)
            calculations.append(CompiledCalculation(definition, expression))
            visiting.pop()
            visited.add(field_id)

        for field_id in requested:
            visit(field_id)

        return cls(
            calculations=tuple(calculations),
            required_api_field_ids=tuple(api_inputs),
            required_module_field_ids=tuple(module_inputs),
        )

    def evaluate(self, values: dict[str, object]) -> dict[str, object]:
        """返回包含原始值和计算结果的新字典，不修改调用方传入的数据。"""
        output = dict(values)
        for calculation in self.calculations:
            definition = calculation.definition
            context = _dependency_context(definition, output)
            if definition.null_policy == "propagate" and any(
                value is None for value in context.values()
            ):
                output[definition.standard_field_id] = None
                continue
            try:
                result = _evaluate_node(calculation.expression.body, context)
                output[definition.standard_field_id] = _normalize_result(definition, result)
            except (ArithmeticError, TypeError, ValueError) as exc:
                raise ConfigurationError(
                    f"计算字段 {definition.standard_field_id} 执行失败：{exc}"
                ) from exc
        return output


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def _compile_expression(definition: FieldDefinition) -> ast.Expression:
    expression_text = definition.calculation_expression or ""
    if len(expression_text) > _MAX_EXPRESSION_LENGTH:
        raise ConfigurationError(
            f"计算字段 {definition.standard_field_id} 的表达式超过"
            f" {_MAX_EXPRESSION_LENGTH} 个字符。"
        )
    try:
        parsed = ast.parse(expression_text, mode="eval")
    except SyntaxError as exc:
        raise ConfigurationError(
            f"计算字段 {definition.standard_field_id} 的表达式语法错误：{exc.msg}"
        ) from exc
    if not isinstance(parsed, ast.Expression):
        raise ConfigurationError(f"计算字段 {definition.standard_field_id} 不是单一表达式。")

    nodes = list(ast.walk(parsed))
    if len(nodes) > _MAX_AST_NODES:
        raise ConfigurationError(
            f"计算字段 {definition.standard_field_id} 的表达式过于复杂。"
        )
    references: set[str] = set()
    for node in nodes:
        if not isinstance(node, _ALLOWED_NODE_TYPES):
            raise ConfigurationError(
                f"计算字段 {definition.standard_field_id} 使用了禁止的语法："
                f"{type(node).__name__}"
            )
        if isinstance(node, ast.Constant) and not (
            node.value is None or isinstance(node.value, (str, int, float, bool))
        ):
            raise ConfigurationError(
                f"计算字段 {definition.standard_field_id} 使用了不支持的常量。"
            )
        if isinstance(node, ast.Name) and node.id not in _FUNCTION_NAMES:
            references.add(node.id)
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTION_NAMES:
                raise ConfigurationError(
                    f"计算字段 {definition.standard_field_id} 调用了未允许的函数。"
                )
            if node.keywords:
                raise ConfigurationError(
                    f"计算字段 {definition.standard_field_id} 不允许使用命名参数。"
                )

    declared = set(definition.dependency_field_ids)
    undeclared = sorted(references - declared)
    unused = sorted(declared - references)
    if undeclared:
        raise ConfigurationError(
            f"计算字段 {definition.standard_field_id} 使用了未声明依赖："
            + "、".join(undeclared)
        )
    if unused:
        raise ConfigurationError(
            f"计算字段 {definition.standard_field_id} 声明了但未使用依赖："
            + "、".join(unused)
        )
    return parsed


def _dependency_context(
    definition: FieldDefinition,
    values: dict[str, object],
) -> dict[str, ExpressionValue]:
    context: dict[str, ExpressionValue] = {}
    missing: list[str] = []
    nulls: list[str] = []
    for field_id in definition.dependency_field_ids:
        if field_id not in values:
            missing.append(field_id)
            value: object = None
        else:
            value = values[field_id]
        if value is None:
            nulls.append(field_id)
        if definition.null_policy == "zero" and value is None:
            value = 0
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise ConfigurationError(
                f"计算字段 {definition.standard_field_id} 的依赖 {field_id} "
                f"不是可计算单值：{type(value).__name__}"
            )
        context[field_id] = value

    if definition.null_policy == "error" and (missing or nulls):
        problems = []
        if missing:
            problems.append("缺少：" + "、".join(missing))
        explicit_nulls = [field_id for field_id in nulls if field_id not in missing]
        if explicit_nulls:
            problems.append("为空：" + "、".join(explicit_nulls))
        raise ConfigurationError(
            f"计算字段 {definition.standard_field_id} 的依赖不可用（"
            + "；".join(problems)
            + "）。"
        )
    return context


def _evaluate_node(node: ast.AST, context: dict[str, ExpressionValue]) -> ExpressionValue:
    if isinstance(node, ast.Constant):
        value = node.value
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        raise ValueError("不支持的常量")
    if isinstance(node, ast.Name):
        if node.id not in context:
            raise ValueError(f"未知字段：{node.id}")
        return context[node.id]
    if isinstance(node, ast.BinOp):
        left = _evaluate_node(node.left, context)
        right = _evaluate_node(node.right, context)
        if isinstance(node.op, ast.Add) and isinstance(left, str) and isinstance(right, str):
            return left + right
        left_number = _number(left)
        right_number = _number(right)
        if isinstance(node.op, ast.Add):
            return left_number + right_number
        if isinstance(node.op, ast.Sub):
            return left_number - right_number
        if isinstance(node.op, ast.Mult):
            return left_number * right_number
        if isinstance(node.op, ast.Div):
            return left_number / right_number
        if isinstance(node.op, ast.FloorDiv):
            return left_number // right_number
        if isinstance(node.op, ast.Mod):
            return left_number % right_number
        raise ValueError("不支持的二元运算")
    if isinstance(node, ast.UnaryOp):
        operand = _evaluate_node(node.operand, context)
        if isinstance(node.op, ast.Not):
            return not bool(operand)
        number = _number(operand)
        if isinstance(node.op, ast.UAdd):
            return number
        if isinstance(node.op, ast.USub):
            return -number
        raise ValueError("不支持的一元运算")
    if isinstance(node, ast.BoolOp):
        values = [_evaluate_node(item, context) for item in node.values]
        if isinstance(node.op, ast.And):
            return all(bool(value) for value in values)
        if isinstance(node.op, ast.Or):
            return any(bool(value) for value in values)
        raise ValueError("不支持的逻辑运算")
    if isinstance(node, ast.Compare):
        left = _evaluate_node(node.left, context)
        for operator, comparator_node in zip(node.ops, node.comparators, strict=True):
            right = _evaluate_node(comparator_node, context)
            if not _compare(left, right, operator):
                return False
            left = right
        return True
    if isinstance(node, ast.IfExp):
        branch = node.body if bool(_evaluate_node(node.test, context)) else node.orelse
        return _evaluate_node(branch, context)
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("只允许调用白名单函数")
        arguments = [_evaluate_node(argument, context) for argument in node.args]
        return _call_function(node.func.id, arguments)
    raise ValueError(f"不支持的表达式节点：{type(node).__name__}")


def _number(value: ExpressionValue) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"需要数字，实际得到 {value!r}")
    if not math.isfinite(float(value)):
        raise ValueError("数字必须是有限值")
    return value


def _compare(left: ExpressionValue, right: ExpressionValue, operator: ast.cmpop) -> bool:
    if isinstance(operator, ast.Eq):
        return left == right
    if isinstance(operator, ast.NotEq):
        return left != right
    if isinstance(left, bool) or isinstance(right, bool):
        raise ValueError("布尔值只支持相等或不相等比较")
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        if isinstance(operator, ast.Lt):
            return left < right
        if isinstance(operator, ast.LtE):
            return left <= right
        if isinstance(operator, ast.Gt):
            return left > right
        if isinstance(operator, ast.GtE):
            return left >= right
    if isinstance(left, str) and isinstance(right, str):
        if isinstance(operator, ast.Lt):
            return left < right
        if isinstance(operator, ast.LtE):
            return left <= right
        if isinstance(operator, ast.Gt):
            return left > right
        if isinstance(operator, ast.GtE):
            return left >= right
    if not (
        isinstance(left, (int, float, str)) and isinstance(right, (int, float, str))
    ):
        raise ValueError("大小比较两侧必须同时为数字或同时为文本")
    raise ValueError("不支持的比较运算")


def _call_function(name: str, arguments: list[ExpressionValue]) -> ExpressionValue:
    if name == "SAFE_DIVIDE":
        _require_arity(name, arguments, 2, 3)
        numerator = _number(arguments[0])
        denominator = _number(arguments[1])
        if denominator == 0:
            return arguments[2] if len(arguments) == 3 else None
        return numerator / denominator
    if name in {"MIN", "MAX"}:
        _require_arity(name, arguments, 1, None)
        numbers = [_number(value) for value in arguments]
        return min(numbers) if name == "MIN" else max(numbers)
    if name == "ROUND":
        _require_arity(name, arguments, 1, 2)
        number = _number(arguments[0])
        digits = 0 if len(arguments) == 1 else int(_number(arguments[1]))
        if digits < 0 or digits > 12:
            raise ValueError("ROUND 的小数位必须在0到12之间")
        return round(number, digits)
    if name == "ABS":
        _require_arity(name, arguments, 1, 1)
        return abs(_number(arguments[0]))
    if name == "FLOOR":
        _require_arity(name, arguments, 1, 1)
        return math.floor(_number(arguments[0]))
    if name == "CEIL":
        _require_arity(name, arguments, 1, 1)
        return math.ceil(_number(arguments[0]))
    if name == "COALESCE":
        _require_arity(name, arguments, 1, None)
        return next((value for value in arguments if value is not None), None)
    if name == "IF":
        _require_arity(name, arguments, 3, 3)
        return arguments[1] if bool(arguments[0]) else arguments[2]
    raise ValueError(f"未注册函数：{name}")


def _require_arity(
    name: str,
    arguments: list[ExpressionValue],
    minimum: int,
    maximum: int | None,
) -> None:
    if len(arguments) < minimum or (maximum is not None and len(arguments) > maximum):
        upper = "不限" if maximum is None else str(maximum)
        raise ValueError(f"{name} 参数数量必须在 {minimum} 到 {upper} 之间")


def _normalize_result(
    definition: FieldDefinition,
    value: ExpressionValue,
) -> ExpressionValue:
    if value is None:
        if definition.null_policy == "error":
            raise ValueError("结果为空，但空值策略要求报错")
        if definition.null_policy == "zero":
            return 0
        return None
    if definition.data_type == "integer":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("结果必须是整数")
        if isinstance(value, float) and not value.is_integer():
            raise ValueError(f"结果 {value!r} 不是整数")
        return int(value)
    if definition.data_type in {"number", "duration"}:
        number = _number(value)
        if definition.output_precision is not None:
            return round(number, definition.output_precision)
        return number
    if definition.data_type in {"string", "url"}:
        if not isinstance(value, str):
            raise ValueError("结果必须是文本")
        return value
    if definition.data_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError("结果必须是布尔值")
        return value
    raise ValueError(f"安全表达式暂不支持输出类型 {definition.data_type}")
