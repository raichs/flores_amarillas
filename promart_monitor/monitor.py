"""Vigila productos de Promart y envía un correo cuando aparece la oferta con tarjeta SIP.

Promart usa VTEX, así que se consulta la API pública del catálogo
(/api/catalog_system/pub/products/search/<slug>/p) y, como respaldo, el HTML
de la página. Se considera que hay oferta SIP cuando el texto "SIP" aparece en
las promociones/teasers/clusters del producto.

Variables de entorno:
  SMTP_USER, SMTP_PASSWORD  cuenta Gmail y contraseña de aplicación
  MAIL_TO                   destinatario (por defecto SMTP_USER)
"""

import json
import os
import re
import smtplib
import sys
import urllib.request
from email.mime.text import MIMEText
from pathlib import Path

PRODUCTS = {
    # slug: si se debe avisar por correo
    "one-piece-artico-negro": True,
    # Producto de referencia que ya tiene la oferta SIP: solo sirve para
    # comprobar en los logs que la detección funciona.
    "one-piece-artico-33429": False,
}

BASE = "https://www.promart.pe"
STATE_FILE = Path(__file__).with_name("state.json")
# Coincide con "Crédito sip", "credito_sip", "credito-sip.png", etc.
SIP_RE = re.compile(r"(?<![a-z])sip(?![a-z])", re.IGNORECASE)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "es-PE,es;q=0.9",
}


def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def sip_snippets(text):
    """Fragmentos de texto alrededor de cada mención de SIP."""
    return [text[max(0, m.start() - 80):m.end() + 80] for m in SIP_RE.finditer(text)]


def check(slug):
    url = f"{BASE}/{slug}/p"
    info = {"slug": slug, "url": url, "name": slug, "price": None,
            "list_price": None, "sip": False, "evidence": []}

    try:
        data = json.loads(fetch(f"{BASE}/api/catalog_system/pub/products/search/{slug}/p"))
    except Exception as exc:
        print(f"[{slug}] error en API: {exc}")
        data = []

    if data:
        product = data[0]
        info["name"] = product.get("productName", slug)
        offer = product["items"][0]["sellers"][0]["commertialOffer"]
        info["price"] = offer.get("Price")
        info["list_price"] = offer.get("ListPrice")
        # Solo se revisan las partes de promociones, no la descripción del producto.
        promo_parts = {
            "clusterHighlights": product.get("clusterHighlights"),
            "productClusters": product.get("productClusters"),
            "Teasers": offer.get("Teasers"),
            "PromotionTeasers": offer.get("PromotionTeasers"),
            "DiscountHighLight": offer.get("DiscountHighLight"),
            "Installments": offer.get("Installments"),
        }
        for key, value in promo_parts.items():
            for snip in sip_snippets(json.dumps(value, ensure_ascii=False)):
                info["evidence"].append(f"API {key}: {snip}")

    if not info["evidence"]:
        try:
            html = fetch(url)
            # Quita scripts de menús/footers genéricos sería ideal; nos quedamos
            # con menciones que hablen de precio/tarjeta para evitar falsos positivos.
            for snip in sip_snippets(html):
                if re.search(r"cr[eé]dito|tarjeta|precio|S/|dscto|descuento", snip, re.IGNORECASE):
                    info["evidence"].append(f"HTML: {snip}")
        except Exception as exc:
            print(f"[{slug}] error en HTML: {exc}")

    info["sip"] = bool(info["evidence"])
    return info


def send_mail(info):
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    to = os.environ.get("MAIL_TO") or user
    if not (user and password):
        print("Faltan SMTP_USER/SMTP_PASSWORD; no se envía correo.")
        return
    body = (
        f"¡Volvió la oferta con tarjeta SIP!\n\n"
        f"Producto: {info['name']}\n"
        f"Precio: S/ {info['price']} (antes S/ {info['list_price']})\n"
        f"Link: {info['url']}\n\n"
        "Detalle detectado:\n" + "\n".join(info["evidence"][:5])
    )
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"Oferta SIP disponible: {info['name']}"
    msg["From"] = user
    msg["To"] = to
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)
    print(f"Correo enviado a {to}")


def main():
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    for slug, notify in PRODUCTS.items():
        info = check(slug)
        print(f"[{slug}] {info['name']} | precio {info['price']} | lista "
              f"{info['list_price']} | SIP={info['sip']}")
        for ev in info["evidence"][:3]:
            print("   ", ev.replace("\n", " "))
        # Avisar solo cuando la oferta pasa de "no hay" a "hay", no en cada corrida.
        if notify and info["sip"] and not state.get(slug):
            send_mail(info)
        state[slug] = info["sip"]
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")
    if "--test-mail" in sys.argv:
        send_mail({"name": "Correo de prueba", "price": "-", "list_price": "-",
                   "url": BASE, "evidence": ["Prueba de configuración"]})


if __name__ == "__main__":
    main()
