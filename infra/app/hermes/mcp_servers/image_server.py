"""Image generation MCP server (stdio transport).

Primary: Stability AI SD3.5 Large on AWS Bedrock us-west-2 (high quality).
Fallback: Pillow-based procedural generation (low quality, always works).
"""
from __future__ import annotations

import base64
import io
import json
import os
import random
import uuid

import boto3
from botocore.exceptions import ClientError
from mcp.server.fastmcp import FastMCP

REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-1"))
SD35_MODEL = "stability.sd3-5-large-v1:0"
SD35_REGION = "us-west-2"

mcp = FastMCP("image")


def _generate_via_bedrock(prompt: str, negative_prompt: str, width: int, height: int, cfg_scale: float) -> bytes | None:
    """Try SD3.5 Large in us-west-2. Returns PNG bytes, or None if unavailable."""
    try:
        bedrock = boto3.client("bedrock-runtime", region_name=SD35_REGION)
        body_dict: dict = {
            "prompt": prompt,
            "output_format": "png",
            "mode": "text-to-image",
        }
        if negative_prompt:
            body_dict["negative_prompt"] = negative_prompt
        # aspect ratio: pick closest standard
        ratio = width / height
        if ratio >= 1.7:
            body_dict["aspect_ratio"] = "16:9"
        elif ratio >= 1.2:
            body_dict["aspect_ratio"] = "4:3"
        elif ratio <= 0.6:
            body_dict["aspect_ratio"] = "9:16"
        elif ratio <= 0.85:
            body_dict["aspect_ratio"] = "3:4"
        else:
            body_dict["aspect_ratio"] = "1:1"

        resp = bedrock.invoke_model(
            modelId=SD35_MODEL,
            body=json.dumps(body_dict),
            contentType="application/json",
            accept="application/json",
        )
        result = json.loads(resp["body"].read())
        finish_reasons = result.get("finish_reasons", [])
        if finish_reasons and finish_reasons[0] not in ("SUCCESS", "success", None):
            print(f"[image_server] SD3.5 finish_reason={finish_reasons[0]}", flush=True)
            return None
        images = result.get("images", [])
        if not images:
            return None
        return base64.b64decode(images[0])
    except Exception as e:
        import traceback
        print(f"[image_server] SD3.5 error: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        return None


def _generate_via_pillow(prompt: str, width: int, height: int) -> bytes:
    """Procedural image using Pillow. Low quality but always available."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        import subprocess
        subprocess.run(["pip", "install", "-q", "pillow"], check=True)
        from PIL import Image, ImageDraw, ImageFont

    rng = random.Random(hash(prompt) & 0xFFFFFFFF)

    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)

    r1, g1, b1 = rng.randint(30, 100), rng.randint(30, 100), rng.randint(80, 180)
    r2, g2, b2 = rng.randint(100, 220), rng.randint(100, 220), rng.randint(100, 220)
    for y in range(height):
        t = y / height
        r = int(r1 + (r2 - r1) * t)
        g = int(g1 + (g2 - g1) * t)
        b = int(b1 + (b2 - b1) * t)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    for i in range(8):
        x = rng.randint(0, width)
        y = rng.randint(0, height)
        r = rng.randint(20, min(width, height) // 3)
        color = (rng.randint(80, 255), rng.randint(80, 255), rng.randint(80, 255))
        draw.ellipse([x - r, y - r, x + r, y + r], fill=color, outline=color)

    words = prompt[:60] + ("..." if len(prompt) > 60 else "")
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
    except Exception:
        font = ImageFont.load_default()
    draw.text((10, height - 40), words, fill=(240, 240, 240), font=font)
    draw.text((10, height - 20), "[Pillow fallback — SD3.5 Large unavailable]", fill=(200, 200, 200), font=font)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


@mcp.tool()
def generate_image(
    prompt: str,
    negative_prompt: str = "low quality, blurry, distorted, watermark",
    width: int = 512,
    height: int = 512,
    cfg_scale: float = 8.0,
) -> dict:
    """Generate an image and save as PNG to /tmp.

    Tries Stability AI SD3.5 Large (high quality, photorealistic) first.
    Falls back to Pillow procedural generation if SD3.5 is unavailable.

    Args:
        prompt: Image description. English preferred for best quality.
        negative_prompt: Elements to exclude from the image.
        width: Width in pixels (used to determine aspect ratio).
        height: Height in pixels (used to determine aspect ratio).
        cfg_scale: Ignored (kept for API compatibility).

    Returns:
        {"file_path": "/tmp/image_<uuid>.png", "backend": "sd35_large"|"pillow", "aspect_ratio": "..."}
        Pass file_path directly to chatwork.upload_file.
    """
    img_bytes = _generate_via_bedrock(prompt, negative_prompt, width, height, cfg_scale)
    backend = "sd35_large"

    if img_bytes is None:
        img_bytes = _generate_via_pillow(prompt, width, height)
        backend = "pillow"

    file_path = f"/tmp/image_{uuid.uuid4().hex[:8]}.png"
    with open(file_path, "wb") as f:
        f.write(img_bytes)

    ratio = width / height
    if ratio >= 1.7:
        aspect = "16:9"
    elif ratio >= 1.2:
        aspect = "4:3"
    elif ratio <= 0.6:
        aspect = "9:16"
    elif ratio <= 0.85:
        aspect = "3:4"
    else:
        aspect = "1:1"

    return {
        "file_path": file_path,
        "backend": backend,
        "aspect_ratio": aspect,
        "note": "" if backend == "sd35_large" else "SD3.5 Large unavailable — Pillow fallback used.",
    }


if __name__ == "__main__":
    mcp.run()
