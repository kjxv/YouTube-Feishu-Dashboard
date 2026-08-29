import os
import requests
from dotenv import load_dotenv

load_dotenv()

APP_ID = os.getenv("LARK_APP_ID")
APP_SECRET = os.getenv("LARK_APP_SECRET")
BASE_TOKEN = os.getenv("LARK_BASE_TOKEN")

url = "https://open.larksuite.com/open-apis/auth/v3/tenant_access_token/internal"

response = requests.post(
    url,
    json={
        "app_id": APP_ID,
        "app_secret": APP_SECRET
    },
    timeout=30
)

data = response.json()

if data.get("code") != 0:
    print("❌ 获取 tenant_access_token 失败")
    print(data)
    raise SystemExit

token = data["tenant_access_token"]

print("✅ tenant_access_token 获取成功")

url = (
    f"https://open.larksuite.com/open-apis/bitable/v1/"
    f"apps/{BASE_TOKEN}/tables"
)

response = requests.get(
    url,
    headers={
        "Authorization": f"Bearer {token}"
    },
    timeout=30
)

data = response.json()

if data.get("code") != 0:
    print("❌ Base 读取失败")
    print(data)
    raise SystemExit

print("")
print("✅ Lark Base 连接成功")
print("")

for table in data["data"]["items"]:
    print(
        table.get("name"),
        " → ",
        table.get("table_id")
    )