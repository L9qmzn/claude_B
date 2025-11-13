from __future__ import annotations

from cc_B.app_factory import create_app
from cc_B.config import CONFIG

app = create_app()


if __name__ == "__main__":
    import uvicorn

    port_value = CONFIG.get("port")
    try:
        port = int(port_value) if port_value is not None else 8207
    except ValueError:
        port = 8207

    uvicorn.run("cc_B.main:app", host="0.0.0.0", port=port, reload=False)
