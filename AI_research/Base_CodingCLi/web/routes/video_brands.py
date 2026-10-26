"""Manage the engine's local brand registry from the research console."""
from fastapi import APIRouter, Depends, HTTPException, Response

from core.video.autovideo import AutoVideoError, AutoVideoUnreachable
from web.deps import require_admin
from web.models import ApiError
from web.services.video_render import get_client

router = APIRouter(prefix="/api/video-brands", tags=["video-brands"])


def call(fn):
    try:
        return fn(get_client())
    except AutoVideoError as exc:
        raise HTTPException(exc.status_code, detail=ApiError.make(exc.code, exc.message)) from exc
    except (AutoVideoUnreachable, ValueError) as exc:
        raise HTTPException(422, detail=ApiError.make("brand_unavailable", str(exc))) from exc


@router.get("")
def listing(user=Depends(require_admin)):
    return call(lambda client: client.brands())


@router.post("/demo/install")
def demo(user=Depends(require_admin)):
    return call(lambda client: client.install_demo_brand())


@router.put("/active")
def active(body: dict, user=Depends(require_admin)):
    return call(lambda client: client.brand_request("PUT", "active", body))


@router.put("/{key}")
def save(key: str, body: dict, user=Depends(require_admin)):
    return call(lambda client: client.brand_request("PUT", key, body))


@router.post("/{key}/assets/{kind}")
def upload(key: str, kind: str, body: dict, user=Depends(require_admin)):
    return call(lambda client: client.brand_request("POST", key, body, kind))


@router.get("/{key}/assets/{kind}")
def preview(key: str, kind: str, user=Depends(require_admin)):
    raw = call(lambda client: client.brand_asset(key, kind))
    return Response(raw, media_type="image/png" if kind == "logo" else "audio/wav",
                    headers={"Cache-Control": "no-store"})
