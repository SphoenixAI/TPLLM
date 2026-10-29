"""Vercel FastAPI entrypoint. Vercel Authentication protects this deployment."""
from tpllm.hosted import create_hosted_app

app = create_hosted_app()
