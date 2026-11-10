from datetime import datetime
from pydantic import BaseModel, EmailStr, ConfigDict, Field

# --- Authentication ---
class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=72, description="Password between 8 and 72 characters")

class UserResponse(BaseModel):
    id: int
    email: EmailStr

    model_config = ConfigDict(from_attributes=True)

class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

# --- Scraper & Comparison ---
class PlatformResult(BaseModel):
    platform: str
    title: str | None = None
    price: float | None = Field(default=None, ge=0)
    image_url: str | None = None
    product_url: str | None = None
    in_stock: bool

class SearchCompareResponse(BaseModel):
    query: str = Field(..., min_length=2)
    amazon: PlatformResult | None = None
    flipkart: PlatformResult | None = None
    cheaper_platform: str | None = None

# --- Tracking & Recharts Price History ---
class TrackProductRequest(BaseModel):
    title: str = Field(..., min_length=2, max_length=500)
    image_url: str | None = Field(default=None, max_length=1000)
    amazon_url: str | None = Field(default=None, max_length=1000, pattern=r"^https?://.*")
    flipkart_url: str | None = Field(default=None, max_length=1000, pattern=r"^https?://.*")
    initial_amazon_price: float | None = Field(default=None, gt=0)
    initial_flipkart_price: float | None = Field(default=None, gt=0)

class PricePoint(BaseModel):
    timestamp: datetime
    amazon_price: float | None = None
    flipkart_price: float | None = None

class ProductHistoryResponse(BaseModel):
    product_id: int
    title: str
    lowest_price: float | None = None
    highest_price: float | None = None
    average_price: float | None = None
    chart_data: list[PricePoint]

# --- Price Alerts ---
class AlertCreate(BaseModel):
    product_id: int = Field(..., gt=0)
    target_price: float = Field(..., gt=0, description="Target price must be greater than 0")

class AlertResponse(BaseModel):
    id: int
    product_id: int
    target_price: float
    is_active: bool

    model_config = ConfigDict(from_attributes=True)