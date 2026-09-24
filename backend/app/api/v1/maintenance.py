from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session

from app.core.security import require_superuser
from app.db.models import User, VaultAuditMode, VaultAuditRun
from app.db.session import get_session
from app.modules.administration import audit_jobs, vault_audit, vault_audit_policy
from app.modules.work import nudge
from app.schemas.audit_policy import AuditPolicyRead, AuditPolicyUpdate, policy_read
from app.schemas.maintenance import (
    VaultAuditCreate,
    VaultAuditFindingRead,
    VaultAuditRunRead,
)

router = APIRouter(prefix="/maintenance", tags=["maintenance"])


@router.post(
    "/audits", response_model=VaultAuditRunRead, status_code=status.HTTP_202_ACCEPTED
)
def start_audit(
    payload: VaultAuditCreate,
    current_user: User = Depends(require_superuser),
    session: Session = Depends(get_session),
) -> VaultAuditRunRead:
    row, created = vault_audit.create_run(session, current_user.id, payload.mode)
    if created:
        # The PENDING run is the intent; the audit source finds it.
        nudge(audit_jobs.DEFINITION)
    return vault_audit.read_run(session, row)


@router.get("/audits", response_model=list[VaultAuditRunRead])
def list_audits(
    limit: int = Query(25, ge=1, le=100),
    _current_user: User = Depends(require_superuser),
    session: Session = Depends(get_session),
) -> list[VaultAuditRunRead]:
    return vault_audit.list_runs(session, limit)


@router.get("/audits/latest", response_model=VaultAuditRunRead)
def latest_audit(
    _current_user: User = Depends(require_superuser),
    session: Session = Depends(get_session),
) -> VaultAuditRunRead:
    row = vault_audit.latest_run(session)
    if row is None:
        raise HTTPException(status_code=404, detail="audit_not_found")
    return vault_audit.read_run(session, row)


@router.get("/audits/{run_id}", response_model=VaultAuditRunRead)
def get_audit(
    run_id: int,
    _current_user: User = Depends(require_superuser),
    session: Session = Depends(get_session),
) -> VaultAuditRunRead:
    row = session.get(VaultAuditRun, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="audit_not_found")
    return vault_audit.read_run(session, row)


@router.post("/audits/{run_id}/cancel", response_model=VaultAuditRunRead)
def cancel_audit(
    run_id: int,
    _current_user: User = Depends(require_superuser),
    session: Session = Depends(get_session),
) -> VaultAuditRunRead:
    row = vault_audit.request_cancel(session, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="audit_not_found")
    return vault_audit.read_run(session, row)


@router.post("/findings/{finding_id}/repair", response_model=VaultAuditFindingRead)
def repair_finding(
    finding_id: int,
    current_user: User = Depends(require_superuser),
    session: Session = Depends(get_session),
) -> VaultAuditFindingRead:
    row = vault_audit.repair_finding(session, finding_id, current_user.id)
    if row is None:
        raise HTTPException(status_code=404, detail="audit_finding_not_found")
    if row.state.value != "resolved":
        raise HTTPException(status_code=409, detail="audit_repair_not_available")
    return vault_audit.finding_read(row)


@router.post("/findings/{finding_id}/ignore", response_model=VaultAuditFindingRead)
def ignore_finding(
    finding_id: int,
    current_user: User = Depends(require_superuser),
    session: Session = Depends(get_session),
) -> VaultAuditFindingRead:
    row = vault_audit.ignore_finding(session, finding_id, current_user.id)
    if row is None:
        raise HTTPException(status_code=404, detail="audit_finding_not_found")
    return vault_audit.finding_read(row)


@router.get("/audit-policies", response_model=list[AuditPolicyRead])
def audit_policies(
    _user: User = Depends(require_superuser), session: Session = Depends(get_session)
) -> list[AuditPolicyRead]:
    return [
        policy_read(
            row,
            estimated_remote_bytes=vault_audit_policy.estimated_remote_bytes(
                session, VaultAuditMode(row.mode)
            ),
        )
        for row in vault_audit_policy.list_policies(session)
    ]


@router.put("/audit-policies/{mode}", response_model=AuditPolicyRead)
def save_audit_policy(
    mode: VaultAuditMode,
    payload: AuditPolicyUpdate,
    user: User = Depends(require_superuser),
    session: Session = Depends(get_session),
) -> AuditPolicyRead:
    policy = vault_audit_policy.update_policy(
        session, mode, payload.model_dump(exclude_unset=True), user.id
    )
    # A new schedule moves the next slot; the pass re-arms its delayed nudge.
    nudge(audit_jobs.DEFINITION)
    return policy_read(policy)


@router.post("/audit-policies/{mode}/skip", response_model=AuditPolicyRead)
def skip_audit(
    mode: VaultAuditMode,
    _user: User = Depends(require_superuser),
    session: Session = Depends(get_session),
) -> AuditPolicyRead:
    policy = vault_audit_policy.skip_once(session, mode, actor_id=_user.id)
    nudge(audit_jobs.DEFINITION)
    return policy_read(policy)
