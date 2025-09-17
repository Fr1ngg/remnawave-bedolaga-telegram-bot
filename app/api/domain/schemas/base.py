from typing import Generic, TypeVar, List

from pydantic import BaseModel

T = TypeVar("T")

class Page(BaseModel, Generic[T]):
    total: int
    page: int
    size: int
    items: List[T]

    class Config:
        arbitrary_types_allowed = True
        orm_mode = True