import os
from flask import Flask

app = Flask(__name__)

@app.get("/")
def index():
    return "OK", 200

@app.get("/health")
def health():
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
