from fastapi import APIRouter, HTTPException, status
from sqlmodel import col, delete, select

from app.deps import CurrentUser, OwnerOnly, SessionDep, SettingsDep, can_manage_staff
from app.models import Availability, Service, Staff, StaffService, TimeOff
from app.schemas import (
    AvailabilityIn,
    AvailabilityWindow,
    ServiceIn,
    ServiceOut,
    ServiceUpdate,
    StaffIn,
    StaffOut,
    StaffServicesIn,
    StaffUpdate,
    TimeOffIn,
    TimeOffOut,
)

router = APIRouter(tags=["catalog"])


def _staff_or_404(session: SessionDep, staff_id: int) -> Staff:
    staff = session.get(Staff, staff_id)
    if staff is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Staff member not found")
    return staff


# ---- services -------------------------------------------------------------


@router.get("/services", response_model=list[ServiceOut])
def list_services(session: SessionDep) -> list[Service]:
    return list(session.exec(select(Service).where(Service.active).order_by(col(Service.id))))


@router.post("/services", response_model=ServiceOut, status_code=status.HTTP_201_CREATED)
def create_service(body: ServiceIn, session: SessionDep, _: OwnerOnly) -> Service:
    service = Service(**body.model_dump())
    session.add(service)
    session.commit()
    session.refresh(service)
    return service


@router.patch("/services/{service_id}", response_model=ServiceOut)
def update_service(
    service_id: int, body: ServiceUpdate, session: SessionDep, _: OwnerOnly
) -> Service:
    service = session.get(Service, service_id)
    if service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Service not found")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(service, key, value)
    session.add(service)
    session.commit()
    session.refresh(service)
    return service


# ---- staff ----------------------------------------------------------------


@router.get("/staff", response_model=list[StaffOut])
def list_staff(session: SessionDep) -> list[Staff]:
    return list(session.exec(select(Staff).where(Staff.active).order_by(col(Staff.id))))


@router.post("/staff", response_model=StaffOut, status_code=status.HTTP_201_CREATED)
def create_staff(body: StaffIn, session: SessionDep, settings: SettingsDep, _: OwnerOnly) -> Staff:
    staff = Staff(name=body.name, timezone=body.timezone or settings.default_timezone)
    session.add(staff)
    session.commit()
    session.refresh(staff)
    return staff


@router.patch("/staff/{staff_id}", response_model=StaffOut)
def update_staff(staff_id: int, body: StaffUpdate, session: SessionDep, _: OwnerOnly) -> Staff:
    staff = _staff_or_404(session, staff_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(staff, key, value)
    session.add(staff)
    session.commit()
    session.refresh(staff)
    return staff


@router.put("/staff/{staff_id}/services", response_model=list[int])
def set_staff_services(
    staff_id: int, body: StaffServicesIn, session: SessionDep, _: OwnerOnly
) -> list[int]:
    _staff_or_404(session, staff_id)
    ids = sorted(set(body.service_ids))
    found = session.exec(select(Service.id).where(col(Service.id).in_(ids))).all()
    if len(found) != len(ids):
        raise HTTPException(422, "Unknown service id")
    session.exec(delete(StaffService).where(col(StaffService.staff_id) == staff_id))
    for service_id in ids:
        session.add(StaffService(staff_id=staff_id, service_id=service_id))
    session.commit()
    return ids


# ---- availability and time off -------------------------------------------


@router.get("/staff/{staff_id}/availability", response_model=list[AvailabilityWindow])
def get_availability(staff_id: int, session: SessionDep) -> list[Availability]:
    _staff_or_404(session, staff_id)
    return list(
        session.exec(
            select(Availability)
            .where(Availability.staff_id == staff_id)
            .order_by(col(Availability.weekday), col(Availability.start_time))
        )
    )


@router.put("/staff/{staff_id}/availability", response_model=list[AvailabilityWindow])
def set_availability(
    staff_id: int, body: AvailabilityIn, session: SessionDep, user: CurrentUser
) -> list[AvailabilityWindow]:
    _staff_or_404(session, staff_id)
    if not can_manage_staff(user, staff_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not allowed for this staff member")
    session.exec(delete(Availability).where(col(Availability.staff_id) == staff_id))
    for w in body.windows:
        session.add(Availability(staff_id=staff_id, **w.model_dump()))
    session.commit()
    return sorted(body.windows, key=lambda w: (w.weekday, w.start_time))


@router.get("/staff/{staff_id}/time-off", response_model=list[TimeOffOut])
def list_time_off(staff_id: int, session: SessionDep, user: CurrentUser) -> list[TimeOff]:
    _staff_or_404(session, staff_id)
    if not can_manage_staff(user, staff_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not allowed for this staff member")
    return list(
        session.exec(
            select(TimeOff).where(TimeOff.staff_id == staff_id).order_by(col(TimeOff.start_utc))
        )
    )


@router.post(
    "/staff/{staff_id}/time-off", response_model=TimeOffOut, status_code=status.HTTP_201_CREATED
)
def add_time_off(staff_id: int, body: TimeOffIn, session: SessionDep, user: CurrentUser) -> TimeOff:
    _staff_or_404(session, staff_id)
    if not can_manage_staff(user, staff_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not allowed for this staff member")
    row = TimeOff(staff_id=staff_id, start_utc=body.start, end_utc=body.end, reason=body.reason)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@router.delete("/staff/{staff_id}/time-off/{time_off_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_time_off(
    staff_id: int, time_off_id: int, session: SessionDep, user: CurrentUser
) -> None:
    if not can_manage_staff(user, staff_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not allowed for this staff member")
    row = session.get(TimeOff, time_off_id)
    if row is None or row.staff_id != staff_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Time off not found")
    session.delete(row)
    session.commit()
