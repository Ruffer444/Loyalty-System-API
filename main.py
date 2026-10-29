# main.py
from fastapi import FastAPI, Depends, HTTPException, Header, Query
from pydantic import BaseModel, field_validator, EmailStr
from typing import Optional, List
from datetime import datetime, date
from sqlalchemy import create_engine, Column, String, Integer, Float, DateTime, Date, ForeignKey, UniqueConstraint
from sqlalchemy.orm import declarative_base, sessionmaker, Session  # исправлено
import uuid
import uvicorn

# ==================== НАСТРОЙКА БД ====================
SQLALCHEMY_DATABASE_URL = "sqlite:///./loyalty.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ==================== МОДЕЛИ БД ====================
class Organization(Base):
    __tablename__ = "organizations"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)

class Customer(Base):
    __tablename__ = "customers"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    email = Column(String, nullable=False)
    total_points = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint('organization_id', 'phone', name='uq_org_phone'),)

class LoyaltyCard(Base):
    __tablename__ = "loyalty_cards"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    customer_id = Column(String, ForeignKey("customers.id"), nullable=False)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    card_type = Column(String, nullable=False)
    issue_date = Column(Date, nullable=False)
    status = Column(String, default="active")
    __table_args__ = (UniqueConstraint('customer_id', 'organization_id', name='uq_customer_org_card'),)

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    customer_id = Column(String, ForeignKey("customers.id"), nullable=False)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    amount_rub = Column(Float, nullable=False)
    points_earned = Column(Integer, nullable=False)
    external_transaction_id = Column(String, unique=True, nullable=False)  # идемпотентность
    timestamp = Column(DateTime, nullable=False)

class Redemption(Base):
    __tablename__ = "redemptions"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    customer_id = Column(String, ForeignKey("customers.id"), nullable=False)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    points_used = Column(Integer, nullable=False)
    service = Column(String, nullable=False)
    external_redemption_id = Column(String, unique=True, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

# ==================== PYDANTIC-СХЕМЫ ====================
class CustomerCreate(BaseModel):
    name: str
    phone: str
    email: EmailStr
    organization_id: str

    @field_validator('phone')  # исправлено
    @classmethod                # добавлено
    def normalize_phone(cls, v):
        digits = ''.join(filter(str.isdigit, v))
        if len(digits) < 10:
            raise ValueError('Некорректный номер телефона')
        return '+' + digits if not v.startswith('+') else v

class CustomerUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[EmailStr] = None

class LoyaltyCardCreate(BaseModel):
    card_type: str
    issue_date: date

class TransactionCreate(BaseModel):
    customer_id: str
    organization_id: str
    amount_rub: float
    points_earned: int
    transaction_id: str
    timestamp: datetime

class RedemptionCreate(BaseModel):
    customer_id: str
    organization_id: str
    points_used: int
    service: str
    redemption_id: str

# ==================== ЗАВИСИМОСТИ ====================
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Упрощённая проверка токенов (в реальности — JWT или БД)
ADMIN_TOKEN = "admin_token"
STAFF_TOKEN = "staff_token"

def verify_token(authorization: Optional[str] = Header(None), required_role: str = "staff"):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    token = authorization.split(" ")[1]
    if required_role == "admin" and token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Admin access required")
    if required_role == "staff" and token not in (ADMIN_TOKEN, STAFF_TOKEN):
        raise HTTPException(status_code=403, detail="Staff or admin access required")
    return token

# ==================== ПРИЛОЖЕНИЕ ====================
app = FastAPI(
    title="Loyalty System API",
    description="RESTful API для системы лояльности (демо-задание финала «Я — профессионал»)",
    version="1.0.0"
)

# ---------- 1. Регистрация клиента ----------
@app.post("/customers", status_code=201)
def create_customer(
    customer: CustomerCreate,
    db: Session = Depends(get_db),
    token: str = Depends(lambda: verify_token(required_role="admin"))
):
    org = db.query(Organization).filter(Organization.id == customer.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Организация не найдена")
    existing = db.query(Customer).filter(
        Customer.organization_id == customer.organization_id,
        Customer.phone == customer.phone
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Клиент с таким телефоном уже есть в организации")
    new_customer = Customer(
        name=customer.name,
        phone=customer.phone,
        email=customer.email,
        organization_id=customer.organization_id
    )
    db.add(new_customer)
    db.commit()
    db.refresh(new_customer)
    return {"id": new_customer.id, "organization_id": new_customer.organization_id, "name": new_customer.name}

# ---------- 2. Создание карты лояльности ----------
@app.post("/customers/{customer_id}/loyalty-card", status_code=201)
def create_loyalty_card(
    customer_id: str,
    card: LoyaltyCardCreate,
    db: Session = Depends(get_db),
    token: str = Depends(lambda: verify_token(required_role="admin"))
):
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    existing_card = db.query(LoyaltyCard).filter(
        LoyaltyCard.customer_id == customer_id,
        LoyaltyCard.organization_id == customer.organization_id
    ).first()
    if existing_card:
        raise HTTPException(status_code=409, detail="У клиента уже есть карта в этой организации")
    new_card = LoyaltyCard(
        customer_id=customer_id,
        organization_id=customer.organization_id,
        card_type=card.card_type,
        issue_date=card.issue_date
    )
    db.add(new_card)
    db.commit()
    db.refresh(new_card)
    return {"card_id": new_card.id, "type": new_card.card_type, "status": new_card.status}

# ---------- 3. Начисление баллов ----------
@app.post("/transactions", status_code=201)
def create_transaction(
    transaction: TransactionCreate,
    db: Session = Depends(get_db),
    token: str = Depends(lambda: verify_token(required_role="staff"))
):
    customer = db.query(Customer).filter(Customer.id == transaction.customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    # Идемпотентность: если транзакция уже была — вернуть текущий баланс
    existing = db.query(Transaction).filter(
        Transaction.external_transaction_id == transaction.transaction_id
    ).first()
    if existing:
        return {
            "success": True,
            "customer_id": customer.id,
            "total_points": customer.total_points,
            "message": "Транзакция уже была обработана (идемпотентность)"
        }
    customer.total_points += transaction.points_earned
    new_tx = Transaction(
        customer_id=transaction.customer_id,
        organization_id=transaction.organization_id,
        amount_rub=transaction.amount_rub,
        points_earned=transaction.points_earned,
        external_transaction_id=transaction.transaction_id,
        timestamp=transaction.timestamp
    )
    db.add(new_tx)
    db.commit()
    db.refresh(customer)
    return {
        "success": True,
        "customer_id": customer.id,
        "total_points": customer.total_points,
        "message": "Баллы успешно начислены"
    }

# ---------- 4. Списание баллов ----------
@app.post("/redemptions", status_code=201)
def create_redemption(
    redemption: RedemptionCreate,
    db: Session = Depends(get_db),
    token: str = Depends(lambda: verify_token(required_role="staff"))
):
    customer = db.query(Customer).filter(Customer.id == redemption.customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    existing = db.query(Redemption).filter(
        Redemption.external_redemption_id == redemption.redemption_id
    ).first()
    if existing:
        return {
            "success": True,
            "customer_id": customer.id,
            "remaining_points": customer.total_points,
            "message": "Списание уже было обработано (идемпотентность)"
        }
    if redemption.points_used > customer.total_points:
        raise HTTPException(
            status_code=400,
            detail=f"Недостаточно баллов: доступно {customer.total_points}, запрошено {redemption.points_used}"
        )
    customer.total_points -= redemption.points_used
    new_redemption = Redemption(
        customer_id=redemption.customer_id,
        organization_id=redemption.organization_id,
        points_used=redemption.points_used,
        service=redemption.service,
        external_redemption_id=redemption.redemption_id
    )
    db.add(new_redemption)
    db.commit()
    db.refresh(customer)
    return {
        "success": True,
        "customer_id": customer.id,
        "remaining_points": customer.total_points,
        "message": "Баллы успешно списаны"
    }

# ---------- 5. Список клиентов организации ----------
@app.get("/organizations/{org_id}/customers")
def get_organization_customers(
    org_id: str,
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    token: str = Depends(lambda: verify_token(required_role="staff"))
):
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Организация не найдена")
    query = db.query(Customer).filter(Customer.organization_id == org_id)
    total = query.count()
    customers = query.offset(offset).limit(limit).all()
    return {
        "customers": [
            {
                "customer_id": c.id,
                "name": c.name,
                "phone": c.phone,
                "points": c.total_points,
                "last_visit": None  # можно добавить подзапрос
            } for c in customers
        ],
        "total": total
    }

# ---------- 6. Обновление данных клиента ----------
@app.put("/customers/{customer_id}")
def update_customer(
    customer_id: str,
    customer_update: CustomerUpdate,
    db: Session = Depends(get_db),
    token: str = Depends(lambda: verify_token(required_role="staff"))
):
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    updated_fields = []
    if customer_update.name is not None:
        customer.name = customer_update.name
        updated_fields.append("name")
    if customer_update.phone is not None:
        customer.phone = customer_update.phone
        updated_fields.append("phone")
    if customer_update.email is not None:
        customer.email = customer_update.email
        updated_fields.append("email")
    db.commit()
    return {"success": True, "updated_fields": updated_fields}

# ---------- 7. Поиск клиента по телефону ----------
@app.get("/customers/search")
def search_customer_by_phone(
    phone: str,
    db: Session = Depends(get_db),
    token: str = Depends(lambda: verify_token(required_role="staff"))
):
    # Нормализация: оставляем только цифры и +
    normalized = '+' + ''.join(filter(str.isdigit, phone))
    customer = db.query(Customer).filter(Customer.phone == normalized).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    card = db.query(LoyaltyCard).filter(LoyaltyCard.customer_id == customer.id).first()
    return {
        "customer_id": customer.id,
        "name": customer.name,
        "phone": customer.phone,
        "points": customer.total_points,
        "loyalty_card": {
            "card_id": card.id,
            "type": card.card_type,
            "status": card.status
        } if card else None,
        "visit_history": []  # можно заполнить из transactions/redemptions
    }

# ---------- 8. Информация о клиенте ----------
@app.get("/customers/{customer_id}")
def get_customer(
    customer_id: str,
    db: Session = Depends(get_db),
    token: str = Depends(lambda: verify_token(required_role="staff"))
):
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    card = db.query(LoyaltyCard).filter(LoyaltyCard.customer_id == customer_id).first()
    transactions = db.query(Transaction).filter(Transaction.customer_id == customer_id).all()
    visit_history = [
        {
            "date": tx.timestamp.date().isoformat(),
            "organization": tx.organization_id,
            "service": "Покупка",
            "points_earned": tx.points_earned
        } for tx in transactions
    ]
    return {
        "customer_id": customer.id,
        "name": customer.name,
        "phone": customer.phone,
        "points": customer.total_points,
        "loyalty_card": {
            "card_id": card.id,
            "type": card.card_type,
            "status": card.status
        } if card else None,
        "visit_history": visit_history
    }

# ---------- 9. Статистика по баллам за период ----------
@app.get("/organizations/{org_id}/analytics")
def get_analytics(
    org_id: str,
    from_date: date = Query(..., alias="from"),
    to_date: date = Query(..., alias="to"),
    db: Session = Depends(get_db),
    token: str = Depends(lambda: verify_token(required_role="admin"))
):
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Организация не найдена")
    if from_date > to_date:
        raise HTTPException(status_code=400, detail="Дата 'from' не может быть позже 'to'")

    tx_query = db.query(Transaction).filter(
        Transaction.organization_id == org_id,
        Transaction.timestamp >= datetime.combine(from_date, datetime.min.time()),
        Transaction.timestamp <= datetime.combine(to_date, datetime.max.time())
    )
    total_points_issued = sum(tx.points_earned for tx in tx_query.all())

    red_query = db.query(Redemption).filter(
        Redemption.organization_id == org_id,
        Redemption.timestamp >= datetime.combine(from_date, datetime.min.time()),
        Redemption.timestamp <= datetime.combine(to_date, datetime.max.time())
    )
    total_redemptions = sum(r.points_used for r in red_query.all())

    new_customers = db.query(Customer).filter(
        Customer.organization_id == org_id,
        Customer.created_at >= datetime.combine(from_date, datetime.min.time()),
        Customer.created_at <= datetime.combine(to_date, datetime.max.time())
    ).count()

    # "Активные" — те, у кого были транзакции за период
    active_customer_ids = set(tx.customer_id for tx in tx_query.all())
    active_customers = len(active_customer_ids)

    return {
        "total_points_issued": total_points_issued,
        "total_redemptions": total_redemptions,
        "new_customers": new_customers,
        "active_customers": active_customers
    }

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)