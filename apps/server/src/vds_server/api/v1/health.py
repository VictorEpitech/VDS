from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class HealthOut(BaseModel):
    status: str


@router.get("/health", response_model=HealthOut)
async def health() -> HealthOut:
    return HealthOut(status="ok")
