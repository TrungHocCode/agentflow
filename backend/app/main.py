from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1 import api_v1_router
from app.db.mongo_client import close_mongo_connection
from app.db.redis_client import close_redis_connection

app = FastAPI(
    title="AgentFlow Platform API",
    description="AI Agent Platform Backend API built with Supervisor-Worker architecture.",
    version="1.0.0"
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API Router
app.include_router(api_v1_router, prefix="/api")

@app.on_event("shutdown")
async def shutdown_event():
    await close_mongo_connection()
    await close_redis_connection()

@app.get("/")
async def root():
    return {"message": "AgentFlow Platform API is running", "docs_url": "/docs"}
