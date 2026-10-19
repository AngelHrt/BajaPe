"""Genera el icono y el logo de BajaPe (ejecutar solo al desarrollar)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SCALE = 4
SIZE = 256
CANVAS = SIZE * SCALE


def rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def build_icon() -> Image.Image:
    # Fondo rojo con un degradado vertical suave.
    background = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    gradient = Image.new("RGBA", (1, CANVAS))
    top = (255, 74, 59, 255)
    bottom = (214, 31, 24, 255)
    for y in range(CANVAS):
        ratio = y / max(1, CANVAS - 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * ratio) for i in range(4))
        gradient.putpixel((0, y), color)
    gradient = gradient.resize((CANVAS, CANVAS))
    background.paste(gradient, (0, 0), rounded_mask(CANVAS, 150 * SCALE))

    draw = ImageDraw.Draw(background)
    white = (255, 255, 255, 255)

    # Flecha de descarga: triangulo + barra inferior.
    draw.polygon(
        [
            (86 * SCALE, 66 * SCALE),
            (170 * SCALE, 66 * SCALE),
            (128 * SCALE, 151 * SCALE),
        ],
        fill=white,
    )
    draw.rounded_rectangle(
        (72 * SCALE, 172 * SCALE, 184 * SCALE, 191 * SCALE),
        radius=9 * SCALE,
        fill=white,
    )

    return background.resize((SIZE, SIZE), Image.LANCZOS)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    icon = build_icon()
    icon.save(ASSETS / "logo.png")
    icon.save(
        ASSETS / "bajape.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"Generado: {ASSETS / 'bajape.ico'}")
    print(f"Generado: {ASSETS / 'logo.png'}")


if __name__ == "__main__":
    main()
