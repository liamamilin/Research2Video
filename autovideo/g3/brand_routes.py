"""Local-only brand management endpoints."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from g2.branding import BrandError
from g3 import brand_profiles


class AssetUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base64: str = Field(max_length=6_000_000)


def router(data_dir):
    routes = APIRouter(prefix="/api/brands", tags=["brands"])

    def call(fn, *args):
        try:
            return fn(data_dir, *args)
        except (BrandError, OSError) as exc:
            raise HTTPException(422, detail={"code": "BRAND_INVALID", "message": str(exc)}) from exc

    @routes.get("")
    def listing():
        return call(brand_profiles.listing)

    @routes.post("/demo/install")
    def install_demo():
        from scripts.install_demo_brand import install
        return call(install)

    @routes.put("/active")
    def active(body: dict):
        return call(brand_profiles.activate, body)

    @routes.put("/{key}")
    def save(key: str, body: dict):
        return call(brand_profiles.save, key, body)

    @routes.post("/{key}/assets/{kind}")
    def upload(key: str, kind: str, body: AssetUpload):
        return call(brand_profiles.upload, key, kind, body.base64)

    @routes.get("/{key}/assets/{kind}")
    def preview(key: str, kind: str):
        path = call(brand_profiles.asset_path, key, kind)
        return FileResponse(path, media_type="image/png" if kind == "logo" else "audio/wav",
                            headers={"Cache-Control": "no-store"})

    return routes
