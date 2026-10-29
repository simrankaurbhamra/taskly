import os
from datetime import datetime
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from supabase import Client, create_client

load_dotenv()

app = FastAPI(title="Todo API")

# Use a LIST of allowed origins (not a set).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "http://localhost:5501",
        "http://127.0.0.1:5501",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")  # Supabase publishable key is OK here.

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set in .env")

# This client is used only to validate incoming access tokens.
auth_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
bearer_scheme = HTTPBearer(auto_error=False)


class TodoCreate(BaseModel):
    title: str
    startdate: datetime
    deadline: datetime


class TodoUpdate(BaseModel):
    title: str | None = None
    completed: bool | None = None
    deadline: datetime | None = None


def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ],
):
    """Validate the Supabase access token sent as Authorization: Bearer <token>."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Please log in first.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    try:
        response = auth_client.auth.get_user(token)
        user = response.user
    except Exception:
        user = None

    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Your login session is invalid or expired. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


def get_user_supabase(token: str) -> Client:
    """Create a request-specific client so each request uses its own user's JWT."""
    client = create_client(SUPABASE_URL, SUPABASE_KEY)
    client.postgrest.auth(token)
    return client


def get_authenticated_client(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ],
    user=Depends(get_current_user),
) -> Client:
    # get_current_user has already validated the token.
    assert credentials is not None
    return get_user_supabase(credentials.credentials)


@app.get("/")
def health_check():
    return {"message": "Todo API is running"}


# 1. READ ALL TASKS
@app.get("/todos")
def get_all_todo(
    user=Depends(get_current_user),
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    client = get_user_supabase(credentials.credentials)
    result = client.table("tod").select("*").execute()
    # RLS should restrict rows to the signed-in user's user_id.
    return result.data


# 2. READ ONE TASK
@app.get("/todos/{todo_id}")
def get_one_todo(
    todo_id: int,
    user=Depends(get_current_user),
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    client = get_user_supabase(credentials.credentials)
    result = (
        client.table("tod")
        .select("*")
        .eq("id", todo_id)
        .eq("user_id", str(user.id))
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=404, detail="Task not found")

    return result.data[0]


# 3. CREATE TASK
@app.post("/todos")
def create_todo(
    todo: TodoCreate,
    user=Depends(get_current_user),
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    title = todo.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Task title cannot be empty")

    if todo.deadline < todo.startdate:
        raise HTTPException(
            status_code=400,
            detail="Deadline must be on or after the start date.",
        )

    client = get_user_supabase(credentials.credentials)
    new_row = {
        "Task": title,
        "Completed": False,
        "Startdate": todo.startdate.isoformat(),
        "Deadline": todo.deadline.isoformat(),
        "user_id": str(user.id),
    }

    result = client.table("tod").insert(new_row).execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="Task could not be created")

    return {"message": "Task saved successfully!", "data": result.data[0]}


# 4. UPDATE TASK
@app.put("/todos/{todo_id}")
def update_todo(
    todo_id: int,
    todo_data: TodoUpdate,
    user=Depends(get_current_user),
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    fields = todo_data.model_dump(exclude_unset=True)
    update_fields = {}

    if not fields:
        raise HTTPException(status_code=400, detail="No update data provided")

    if "title" in fields:
        if fields["title"] is None or not fields["title"].strip():
            raise HTTPException(status_code=400, detail="Task title cannot be empty")
        update_fields["Task"] = fields["title"].strip()

    if "completed" in fields:
        if fields["completed"] is None:
            raise HTTPException(status_code=400, detail="Completed must be true or false")
        update_fields["Completed"] = fields["completed"]

    if "deadline" in fields:
        if fields["deadline"] is None:
            raise HTTPException(status_code=400, detail="Deadline cannot be empty")
        update_fields["Deadline"] = fields["deadline"].isoformat()

    client = get_user_supabase(credentials.credentials)
    result = (
        client.table("tod")
        .update(update_fields)
        .eq("id", todo_id)
        .eq("user_id", str(user.id))
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=404, detail="Task not found to update")

    return {"message": "Task updated successfully!", "data": result.data[0]}


# 5. DELETE TASK
@app.delete("/todos/{todo_id}")
def delete_todo(
    todo_id: int,
    user=Depends(get_current_user),
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    client = get_user_supabase(credentials.credentials)
    result = (
        client.table("tod")
        .delete()
        .eq("id", todo_id)
        .eq("user_id", str(user.id))
        .select("id")
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=404, detail="Task not found to delete")

    return {"message": "Task deleted successfully", "id": todo_id}
