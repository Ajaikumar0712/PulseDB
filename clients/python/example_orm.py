"""
PulseDB Python ORM — complete usage example.

Run:
    # Start server first
    # .\target\release\pulsedb-server.exe --no-auth

    python example_orm.py
"""

import os
from pulsedb.orm import (
    connect,
    Model, IntField, FloatField, TextField, BoolField, VectorField, JsonField,
)


# ── 1. Connect ────────────────────────────────────────────────────────────────

db = connect(
    "127.0.0.1", 7878,
    # username=os.environ["PULSEDB_USER"],
    # password=os.environ["PULSEDB_PASSWORD"],
)


# ── 2. Define models ──────────────────────────────────────────────────────────

class User(Model):
    class Meta:
        db    = db
        table = "users"

    id     = IntField(primary_key=True)
    name   = TextField()
    age    = IntField()
    active = BoolField(default=True)
    score  = FloatField(default=0.0)


class Product(Model):
    class Meta:
        db    = db
        table = "products"

    id        = IntField(primary_key=True)
    name      = TextField()
    price     = FloatField()
    tags      = JsonField(nullable=True)
    embedding = VectorField(nullable=True)


# ── 3. Schema ─────────────────────────────────────────────────────────────────

User.create_table()
Product.create_table()
User.create_index("age")
Product.create_index("price")


# ── 4. Write ──────────────────────────────────────────────────────────────────

alice = User.create(id=1, name="Alice", age=30, active=True, score=0.95)
bob   = User.create(id=2, name="Bob",   age=17, active=True, score=0.72)
carol = User.create(id=3, name="Carol", age=25, active=False, score=0.88)

print("Created:", alice, bob, carol)

# Bulk insert with transaction
with db.transaction():
    for i in range(4, 10):
        User.create(id=i, name=f"User{i}", age=20 + i, active=i % 2 == 0)

# Products
p1 = Product.create(
    id=1, name="Widget", price=9.99,
    tags={"category": "tools"},
    embedding=[0.9, 0.1, 0.2, 0.0],
)
Product.create(id=2, name="Gadget", price=24.99, embedding=[0.1, 0.8, 0.3, 0.5])
Product.create(id=3, name="Doohickey", price=4.99, embedding=[0.5, 0.5, 0.5, 0.5])

print(f"Inserted {Product.count()} products")


# ── 5. Read ───────────────────────────────────────────────────────────────────

# All rows
all_users = User.all()
print(f"\nAll users ({len(all_users)}):")
for u in all_users:
    print(f"  {u.id:2d}  {u.name:<10}  age={u.age}  active={u.active}")

# Filter with lookup suffixes
adults = User.filter(age__gte=18, active=True).order_by("age").limit(5).all()
print(f"\nActive adults (top 5 by age): {[u.name for u in adults]}")

# Single row
alice = User.get(id=1)
print(f"\nGet id=1: {alice.name}, age={alice.age}")

# .first()
youngest = User.filter(active=True).order_by("age").first()
print(f"Youngest active user: {youngest.name} (age {youngest.age})")

# Exclude
non_alice = User.exclude(id=1).order_by("name").all()
print(f"All except Alice: {[u.name for u in non_alice]}")

# .in lookup
specific = User.filter(id__in=[1, 2, 3]).all()
print(f"id in [1,2,3]: {[u.name for u in specific]}")


# ── 6. Update ─────────────────────────────────────────────────────────────────

# Update via QuerySet
User.filter(id=2).update(active=True, score=0.85)

# Update via instance
bob = User.get(id=2)
bob.update(age=18)
print(f"\nBob updated: age={bob.age}, active={bob.active}")

# Instance save
carol = User.get(id=3)
carol.active = True
carol.save()
print(f"Carol reactivated: active={User.get(id=3).active}")


# ── 7. Delete ─────────────────────────────────────────────────────────────────

User.create(id=99, name="Temp", age=0)
User.filter(id=99).delete()
print(f"\nAfter deleting id=99: {User.count()} users remain")

# Delete via instance
User.create(id=100, name="Temp2", age=0)
temp = User.get(id=100)
temp.delete()
print(f"After instance delete: {User.count()} users remain")


# ── 8. Vector similarity search ───────────────────────────────────────────────

query_vec = [0.88, 0.12, 0.25, 0.05]
nearest = Product.similar("embedding", query_vec, k=3)
print(f"\nVector search (nearest to {query_vec}):")
for p in nearest:
    score = getattr(p, "_score", "?")
    print(f"  {p.name:<12} price={p.price}  score={score}")


# ── 9. Fuzzy text search ──────────────────────────────────────────────────────

hits = User.fuzzy("name", "alic", limit=5)
print(f"\nFuzzy search 'alic': {[u.name for u in hits]}")


# ── 10. QuerySet chaining ─────────────────────────────────────────────────────

qs = (
    User.objects()
    .filter(age__gte=20)
    .filter(active=True)
    .order_by("-score")
    .limit(3)
)
print(f"\nTop 3 active users by score (age>=20):")
for u in qs:
    print(f"  {u.name:<10}  score={u.score}  age={u.age}")


# ── 11. to_dict / repr ────────────────────────────────────────────────────────

print(f"\nalice.to_dict() = {alice.to_dict()}")
print(f"repr(alice)     = {alice!r}")


# ── 12. Transactions ──────────────────────────────────────────────────────────

try:
    with db.transaction():
        User.create(id=200, name="TxUser", age=25)
        raise ValueError("rollback test")
except ValueError:
    pass

exists = User.filter(id=200).first()
print(f"\nTransaction rollback test: id=200 exists = {exists is not None}")  # False


db.close()
print("\nDone.")
