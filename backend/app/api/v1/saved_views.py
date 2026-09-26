from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlmodel import Session

from app.core.security import require_auth, require_user
from app.db.models import User
from app.db.session import get_session
from app.modules.library import saved_views
from app.schemas.saved_views import SavedViewCreate, SavedViewRead, SavedViewUpdate

router = APIRouter(prefix="/saved-views", tags=["saved views"])


@router.get("", response_model=list[SavedViewRead])
def list_saved_views(
    current_user: User = Depends(require_user), session: Session = Depends(get_session)
):
    return saved_views.list_for_user(session, current_user.id)


@router.post(
    "",
    response_model=SavedViewRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_auth)],
)
def create_saved_view(
    payload: SavedViewCreate,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    try:
        return saved_views.create(session, current_user.id, payload)
    except saved_views.SavedViewConflict:
        raise HTTPException(status_code=409, detail="saved_view_name_exists") from None


@router.get("/{view_id}", response_model=SavedViewRead)
def get_saved_view(
    view_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    result = saved_views.get_for_user(session, current_user.id, view_id)
    if result is None:
        raise HTTPException(status_code=404, detail="saved_view_not_found")
    return result


@router.patch(
    "/{view_id}", response_model=SavedViewRead, dependencies=[Depends(require_auth)]
)
def update_saved_view(
    view_id: int,
    payload: SavedViewUpdate,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    try:
        result = saved_views.update(session, current_user.id, view_id, payload)
    except saved_views.SavedViewConflict:
        raise HTTPException(status_code=409, detail="saved_view_name_exists") from None
    if result is None:
        raise HTTPException(status_code=404, detail="saved_view_not_found")
    return result


@router.delete(
    "/{view_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_auth)],
)
def delete_saved_view(
    view_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    if not saved_views.delete(session, current_user.id, view_id):
        raise HTTPException(status_code=404, detail="saved_view_not_found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
