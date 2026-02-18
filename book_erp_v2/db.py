import os
from sqlmodel import SQLModel, create_engine

DB_URL = os.getenv("DB_URL", "sqlite:///./book_erp_v2.db")
engine = create_engine(DB_URL, echo=False)

def init_db():
    SQLModel.metadata.create_all(engine)