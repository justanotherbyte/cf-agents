from flask import Flask, Response, request

from workers import wsgi

app = Flask(__name__)


@app.get("/")
def root():
    return {"greeting": request.environ["workers.env"].GREETING}


@app.post("/echo")
def echo():
    return {"echo": request.get_data(as_text=True)}


@app.get("/stream")
def stream():
    return Response((f"{i}\n" for i in range(3)), mimetype="text/plain")


Default = wsgi.entrypoint(app)
