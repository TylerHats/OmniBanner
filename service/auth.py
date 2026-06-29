import httpx
import pyotp
from datetime import datetime, timedelta
from jose import jwt, JWTError
from passlib.context import CryptContext
from fastapi import HTTPException, status

SECRET_KEY = "OMNIBANNER_SUPER_SECRET_CHANGE_THIS_IN_PROD_1234"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 1440 # 1 day session

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: timedelta = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None

# TOTP Operations
def generate_totp_secret() -> str:
    return pyotp.random_base32()

def get_totp_uri(secret: str, username: str, issuer_name: str = "OmniBanner") -> str:
    return pyotp.totp.TOTP(secret).provisioning_uri(name=username, issuer_name=issuer_name)

def verify_totp_code(secret: str, code: str) -> bool:
    totp = pyotp.TOTP(secret)
    return totp.verify(code)

# OIDC Operations
async def discover_oidc_endpoints(issuer_url: str) -> dict:
    # OIDC Discovery endpoint: https://issuer.com/.well-known/openid-configuration
    # Strip trailing slash if present
    issuer_url = issuer_url.rstrip("/")
    discovery_url = f"{issuer_url}/.well-known/openid-configuration"
    async with httpx.AsyncClient(verify=False) as client:
        try:
            response = await client.get(discovery_url, timeout=10.0)
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            print(f"OIDC Discovery failed for {discovery_url}: {e}")
    return None

async def exchange_oidc_code(token_endpoint: str, client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    async with httpx.AsyncClient(verify=False) as client:
        try:
            response = await client.post(token_endpoint, data=data, timeout=10.0)
            if response.status_code == 200:
                return response.json()
            else:
                print(f"OIDC Token Exchange failed status {response.status_code}: {response.text}")
        except Exception as e:
            print(f"OIDC Token Exchange HTTP error: {e}")
    return None

async def fetch_oidc_userinfo(userinfo_endpoint: str, access_token: str) -> dict:
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(verify=False) as client:
        try:
            response = await client.get(userinfo_endpoint, headers=headers, timeout=10.0)
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            print(f"OIDC Userinfo fetch error: {e}")
    return None
