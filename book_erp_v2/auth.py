import os
from datetime import datetime, timedelta
from typing import Callable

from fastapi import Request, HTTPException
from jose import jwt, JWTError
from passlib.context import CryptContext

SECRET_KEY = os.getenv("SECRET_KEY", "CHANGE_ME_SECRET")
ALGO = "HS256"
ACCESS_TOKEN_MIN = 60 * 24  # 1 day

pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_pw(p: str) -> str:
    return pwd.hash(p)

def verify_pw(p: str, h: str) -> bool:
    return pwd.verify(p, h)

def create_token(username: str, role: str) -> str:
    exp = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_MIN)
    return jwt.encode({"sub": username, "role": role, "exp": exp}, SECRET_KEY, algorithm=ALGO)

def decode_token(token: str) -> dict:
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGO])

def require_roles(*roles: str) -> Callable:
    def guard(request: Request):
        token = request.cookies.get("token")
        if not token:
            raise HTTPException(401, "Login required")
        try:
            data = decode_token(token)
        except JWTError:
            raise HTTPException(401, "Invalid login")
        if roles and data.get("role") not in roles:
            raise HTTPException(403, "Not allowed")
        request.state.user = data
        return data
    return guard