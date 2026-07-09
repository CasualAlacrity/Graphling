from pydantic import BaseModel, Field, field_validator


class UEXTradeData(BaseModel):
    terminal_name: str
    star_system_name: str | None
    orbit_name: str | None
    moon_name: str | None
    planet_name: str | None
    price_you_pay_to_acquire: float | None = Field(validation_alias="price_buy")
    price_you_receive_when_selling: float | None = Field(validation_alias="price_sell")

    @field_validator("price_you_pay_to_acquire", "price_you_receive_when_selling")
    @classmethod
    def zero_means_not_offered(cls, value: float) -> float | None:
        return None if value == 0 else value
