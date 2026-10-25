import os
import csv
import json
import calendar
import hashlib
import hmac
import secrets
import shutil
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime, date, time as datetime_time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from PIL import Image, ImageTk, ImageOps
except ImportError:
    Image = None
    ImageTk = None
    ImageOps = None

APP_DIR = Path(__file__).resolve().parent
DATA_FILE = APP_DIR / "washapp.json"
RECEIPT_DIR = APP_DIR / "receipts"
QR_DIR = APP_DIR / "qr_codes"
PAYMENT_IMAGE_DIR = APP_DIR / "payment_references"
PAYMENT_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
MAX_PAYMENT_IMAGE_BYTES = 5 * 1024 * 1024
PAYMENT_QR_IMAGES = {
    "GCash": APP_DIR / "gcashQr.png",
    "Bank Transfer": APP_DIR / "bankTransferQr.png",
}
PAYMENT_METHODS = list(PAYMENT_QR_IMAGES)
REFUND_METHODS = ["GCash", "Bank Transfer"]
REFUND_STATUSES = ["Pending", "Under Review", "Processing", "Completed", "Rejected"]
PAYMENT_OPTION_DOWNPAYMENT = "Downpayment (30%)"

# Shared dark navy and teal color palette used throughout the interface.
COLORS = {
    "bg": "#0b1422",
    "card": "#142235",
    "card2": "#1b2d43",
    "button": "#176b87",
    "button2": "#2b4058",
    "cyan": "#39c6df",
    "green": "#35b779",
    "muted": "#9aadc2",
    "red": "#e05b68",
    "white": "#f4f8fc",
    "yellow": "#f2c14e",
}

# Central reservation-status palette. Payment statuses use their own styling.
RESERVATION_STATUS_COLORS = {
    "Pending": "#F59E0B",
    "Refunded": "#8B5CF6",
    "Rejected": "#B91C1C",
    "Approved": "#3B82F6",
    "Completed": "#16A34A",
}


# Core application configuration. Keep these values in one place so every
# screen and the JSON store use the same reservation rules.
VEHICLES = ["Sedan", "SUV", "Van", "Pick-up", "Motorcycle"]
BAYS = ["Bay 1", "Bay 2", "Bay 3", "Bay 4"]
TIMES = ([f"{hour}:00 AM" for hour in range(7, 12)] +
         ["12:00 PM"] + [f"{hour}:00 PM" for hour in range(1, 6)])
# Canonical catalogue for new bookings. Existing reservation totals are kept as
# historical snapshots; this catalogue controls new bookings and price previews.
SERVICE_PRICE_DEFAULTS = {
    "Basic Wash": {"Sedan": 130.0, "SUV": 180.0, "Van": 200.0, "Pick-up": 200.0},
    "Wash + Vacuum": {"Sedan": 160.0, "SUV": 200.0, "Van": 230.0, "Pick-up": 230.0},
    "Wash + Wax": {"Sedan": 250.0, "SUV": 300.0, "Van": 350.0, "Pick-up": 350.0},
    "Premium Wash": {"Sedan": 350.0, "SUV": 400.0, "Van": 450.0, "Pick-up": 450.0},
    "Engine Wash": {"Sedan": 450.0, "SUV": 500.0, "Van": 550.0, "Pick-up": 550.0},
    "Basic Motorcycle Wash": {"Motorcycle": 80.0},
    "Wash + Tire Black": {"Motorcycle": 100.0},
    "Premium Motorcycle Wash": {"Motorcycle": 150.0},
    "Motorcycle Wash + Wax": {"Motorcycle": 250.0},
    "Motorcycle Detailing": {"Motorcycle": 500.0},
}
PRICES = {name: dict(prices) for name, prices in SERVICE_PRICE_DEFAULTS.items()}
SERVICES = list(PRICES)
SERVICE_DESCRIPTIONS = {
    "Basic Wash": "Exterior wash and basic cleaning.",
    "Wash + Vacuum": "Exterior wash with interior vacuuming.",
    "Wash + Wax": "Exterior wash and wax finish.",
    "Premium Wash": "Premium exterior and interior cleaning.",
    "Engine Wash": "Engine bay cleaning service.",
    "Basic Motorcycle Wash": "Basic motorcycle wash.",
    "Wash + Tire Black": "Motorcycle wash with tire-black finish.",
    "Premium Motorcycle Wash": "Premium motorcycle wash.",
    "Motorcycle Wash + Wax": "Motorcycle wash with wax finish.",
    "Motorcycle Detailing": "Detailed motorcycle cleaning service.",
}
ACCOUNT_ROLES = ("customer", "staff", "admin")
ACCOUNT_STATUSES = ("Active", "Inactive")
DOWNPAYMENT_RATE = 0.30
PAYMENT_OPTION_FULL = "Full Payment"
DOWNPAYMENT_COMPLETED_MESSAGE = "The 30% downpayment has already been completed."
DOWNPAYMENT_EXCEEDED_MESSAGE = "The verified payment is already greater than the required 30% downpayment."
ERROR_COLOR = COLORS["red"]
DEFAULT_SYSTEM_SETTINGS = {
    "system_name": "WASHAPP",
    "system_subtitle": "Smart Carwash Reservation System",
    "tagline": "Book your carwash with ease.",
    "business_name": "WASHAPP Carwash",
    "contact_info": "",
}


def business_now():
    """Return the current local business time, using Philippine time by default."""
    try:
        return datetime.now(ZoneInfo("Asia/Manila"))
    except (ZoneInfoNotFoundError, OSError):
        # Windows installations without the IANA timezone database still run.
        return datetime.now().astimezone()


def slot_is_future(booking_date, booking_time, now=None):
    """Validate a booking date/time in the formats displayed by the reservation UI."""
    try:
        day = booking_date if isinstance(booking_date, date) else datetime.strptime(
            str(booking_date).strip(), "%m/%d/%Y").date()
        if isinstance(booking_time, datetime_time):
            slot_time = booking_time
        else:
            raw_time = str(booking_time or "").strip()
            slot_time = None
            for pattern in ("%I:%M %p", "%I %p", "%H:%M"):
                try:
                    slot_time = datetime.strptime(raw_time, pattern).time()
                    break
                except ValueError:
                    continue
            if slot_time is None:
                return False
        current = now or business_now()
        if isinstance(current, datetime) and current.tzinfo is not None:
            try:
                current = current.astimezone(ZoneInfo("Asia/Manila")).replace(tzinfo=None)
            except (ZoneInfoNotFoundError, OSError):
                current = current.astimezone().replace(tzinfo=None)
        elif isinstance(current, datetime) and current.tzinfo is None:
            # A supplied naive datetime is treated as local business time.
            pass
        else:
            current = business_now().replace(tzinfo=None)
        return datetime.combine(day, slot_time) > current
    except (TypeError, ValueError, OverflowError):
        return False


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 180_000)
    return salt.hex() + "$" + digest.hex()


def verify_password(password, stored):
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), 180_000)
        return hmac.compare_digest(candidate.hex(), digest_hex)
    except (ValueError, TypeError):

        return stored == password


def money(value):
    return f"₱{float(value):,.2f}"


def cancellation_fee(total):
    """Return the 30% non-refundable cancellation fee, rounded to cents."""
    return round(max(0.0, float(total or 0)) * 0.30, 2)


def cancellation_refund_amount(total, actual_paid):
    """Refund actual verified payments after deducting 30% of the total booking price."""
    return round(max(0.0, float(actual_paid or 0) - cancellation_fee(total)), 2)


def format_timestamp(value):
    """Return a readable date and time for an ISO timestamp."""
    if not value:
        return "N/A"
    try:
        return datetime.fromisoformat(str(value)).strftime("%m/%d/%Y %I:%M:%S %p")
    except ValueError:
        return str(value)


def payment_status_label(total, confirmed, pending):
    """Derive the payment status from confirmed and still-pending amounts."""
    if confirmed > 0:
        return "Fully Paid" if confirmed + 0.005 >= float(total) else "Partially Paid"
    if pending > 0:
        return "Pending"
    return "Unpaid"


def service_name(display_value):
    """Return the service key from a dropdown label that may include its price."""
    return str(display_value).split(" — ", 1)[0]

def service_names_for_vehicle(vehicle):
    """Return only services explicitly offered for the selected vehicle type."""
    return [service for service in SERVICES
            if service in PRICES and vehicle in PRICES[service]]


def service_options(vehicle):
    """Build dropdown labels with the correct vehicle-specific prices."""
    return [f"{service} — {money(PRICES[service][vehicle])}"
            for service in service_names_for_vehicle(vehicle)]


def format_file_size(size):
    """Return a short human-readable file size."""
    size = int(size or 0)
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{max(1, size // 1024)} KB"


def refund_account_error(method, holder, account_number, bank_name=""):
    """Validate the account fields required for GCash or Bank Transfer."""
    method = str(method or "").strip()
    holder = " ".join(str(holder or "").split())
    account_number = str(account_number or "").strip()
    bank_name = " ".join(str(bank_name or "").split())
    digits = "".join(ch for ch in account_number if ch.isdigit())
    if method not in REFUND_METHODS:
        return "Please select GCash or Bank Transfer as the refund method."
    if len(holder) < 2:
        return "Please enter the account holder's full name."
    if method == "GCash":
        if not (10 <= len(digits) <= 13):
            return "Enter a valid GCash mobile number (10-13 digits)."
    else:
        if len(bank_name) < 2 or not any(ch.isalpha() for ch in bank_name):
            return "Enter a valid bank name."
        if not (6 <= len(digits) <= 20):
            return "Enter a valid bank account number (6-20 digits)."
    return ""


def refund_details_complete(method, holder, account_number, bank_name=""):
    """Whether a refund destination has all fields required for its method."""
    return refund_account_error(method, holder, account_number, bank_name) == ""


def normalize_refund_date(value):
    """Accept MM/DD/YYYY or YYYY-MM-DD and return an ISO date, or None if invalid."""
    text = str(value or "").strip()
    if not text:
        return ""
    for pattern in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    return None


def format_refund_date(value, fallback="Not recorded"):
    """Display an ISO refund date in the app's usual month/day/year format."""
    text = str(value or "").strip()
    if not text:
        return fallback
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%m/%d/%Y")
    except ValueError:
        return text


def mask_account_number(value):
    """Mask an account number while keeping a short recognizable prefix/suffix."""
    text = str(value or "").strip()
    if len(text) <= 4:
        return "•" * len(text)
    if len(text) <= 8:
        return text[:2] + ("•" * (len(text) - 4)) + text[-2:]
    return text[:4] + ("•" * max(4, len(text) - 7)) + text[-3:]


def validate_payment_image(path):
    """Validate a payment reference image and return (ok, message)."""
    source = Path(str(path or ""))
    if not source.is_file():
        return False, "Please select a payment reference image from your device."
    extension = source.suffix.lower()
    if extension not in PAYMENT_IMAGE_EXTENSIONS:
        return False, "Invalid file type. Please upload a PNG, JPG, or JPEG image."
    try:
        size = source.stat().st_size
    except OSError:
        return False, "The selected image could not be read. Please choose another file."
    if size <= 0:
        return False, "The selected image is empty. Please choose a valid payment receipt image."
    if size > MAX_PAYMENT_IMAGE_BYTES:
        return False, (f"The selected image is too large ({format_file_size(size)}). "
                       f"Please upload an image no larger than {format_file_size(MAX_PAYMENT_IMAGE_BYTES)}.")
    try:
        with source.open("rb") as handle:
            header = handle.read(12)
    except OSError:
        return False, "The selected image could not be read. Please choose another file."
    is_png = header.startswith(b"\x89PNG\r\n\x1a\n")
    is_jpeg = header.startswith(b"\xff\xd8\xff")
    if extension == ".png" and not is_png:
        return False, "The selected file is not a valid PNG image."
    if extension in (".jpg", ".jpeg") and not is_jpeg:
        return False, "The selected file is not a valid JPG/JPEG image."
    if Image is not None:
        try:
            with Image.open(source) as img:
                img.verify()
        except (OSError, ValueError):
            return False, "The selected file is not a valid image. Please choose another PNG, JPG, or JPEG file."
    return True, ""


def load_payment_image(path, max_width=320, max_height=200):
    """Load a payment image for preview, scaled to fit the given bounds."""
    source = Path(str(path or ""))
    if not source.is_file():
        return None
    if Image is not None and ImageTk is not None:
        try:
            with Image.open(source) as img:
                if ImageOps is not None:
                    img = ImageOps.exif_transpose(img)
                img = img.convert("RGB")
                img.thumbnail((max_width, max_height))
                return ImageTk.PhotoImage(img)
        except (OSError, ValueError):
            return None
    try:
        photo = tk.PhotoImage(file=str(source))
    except tk.TclError:
        return None
    width, height = photo.width(), photo.height()
    factor = max(1, (width + max_width - 1) // max_width, (height + max_height - 1) // max_height)
    if factor > 1:
        photo = photo.subsample(factor, factor)
    return photo


def load_payment_qr(method, subsample=2):
    """Load the QR image that belongs to the chosen payment method (2x2 subsample)."""
    path = PAYMENT_QR_IMAGES.get(method)
    if path is None or not path.exists():
        return None
    try:
        photo = tk.PhotoImage(file=str(path))
        if subsample > 1:
            photo = photo.subsample(subsample, subsample)
        return photo
    except tk.TclError:
        if Image is None or ImageTk is None:
            return None
        try:
            with Image.open(path) as img:
                img = img.convert("RGB")
                if subsample > 1:
                    img = img.reduce(subsample)
                return ImageTk.PhotoImage(img)
        except (OSError, ValueError):
            return None


def ensure_dirs():
    RECEIPT_DIR.mkdir(exist_ok=True)
    QR_DIR.mkdir(exist_ok=True)
    PAYMENT_IMAGE_DIR.mkdir(exist_ok=True)


class Store:
    """Keeps every record in a plain JSON file (washapp.json) - no database engine needed."""

    def __init__(self):
        ensure_dirs()
        self.data = {"users": [], "reservations": [], "payments": [], "refunds": [],
                     "notifications": [], "settings": {}, "services": []}
        self.load()
        self._migrate_configuration()
        self.seed_accounts()
        self.apply_service_catalog()

    def load(self):
        """Read the JSON data file into memory; an unreadable file starts empty."""
        if not DATA_FILE.exists():
            return
        try:
            loaded = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(loaded, dict):
            return
        for key in self.data:
            if isinstance(loaded.get(key), list):
                self.data[key] = loaded[key]
        if isinstance(loaded.get("settings"), dict):
            self.data["settings"] = dict(loaded["settings"])
        self._migrate_bank_transfer_fields()
        self._migrate_configuration()
        self.apply_service_catalog()

    def _migrate_configuration(self):
        """Add settings, service pricing, account statuses, and notification keys safely."""
        changed = False
        for key, value in DEFAULT_SYSTEM_SETTINGS.items():
            if not str(self.data["settings"].get(key) or "").strip():
                self.data["settings"][key] = value
                changed = True
        # Migrate the editable catalogue to the required ten vehicle-specific
        # services and exact prices. Reservation records are not rewritten, so
        # their historical service names and totals remain intact.
        existing_services = list(self.data.get("services") or [])
        existing_by_name = {
            str(item.get("name") or "").strip().casefold(): item
            for item in existing_services if isinstance(item, dict)
        }
        migrated_services = []
        used_ids = set()
        for index, (name, prices) in enumerate(SERVICE_PRICE_DEFAULTS.items(), start=1):
            previous = existing_by_name.get(name.casefold(), {})
            service_id = str(previous.get("service_id") or f"SRV-{index:03d}")
            if service_id in used_ids:
                service_id = f"SRV-{index:03d}"
            used_ids.add(service_id)
            migrated_services.append({
                "service_id": service_id,
                "name": name,
                "description": SERVICE_DESCRIPTIONS.get(name, ""),
                "prices": {vehicle: float(prices[vehicle]) for vehicle in prices},
                "available": True,
            })
        if self.data.get("services") != migrated_services:
            self.data["services"] = migrated_services
            changed = True
        for user in self.data["users"]:
            if not user.get("status"):
                user["status"] = "Active"
                changed = True
            if "email" not in user:
                user["email"] = ""
                changed = True
        if changed:
            self.save()
        self.apply_service_catalog()

    def settings(self):
        return dict(self.data.get("settings") or DEFAULT_SYSTEM_SETTINGS)

    def setting(self, key):
        return self.settings().get(key, DEFAULT_SYSTEM_SETTINGS.get(key, ""))

    def save_settings(self, values, actor):
        account = self.user(actor)
        if not account or account.get("role") != "admin":
            return False, "Only an administrator can change system settings."
        cleaned = {}
        for key in DEFAULT_SYSTEM_SETTINGS:
            cleaned[key] = " ".join(str(values.get(key) or "").split())
        if not cleaned["system_name"]:
            return False, "System name is required."
        if not cleaned["system_subtitle"]:
            return False, "System subtitle is required."
        if not cleaned["tagline"]:
            return False, "Tagline is required."
        self.data["settings"] = cleaned
        self.save()
        return True, "System branding has been saved."

    def reset_settings(self, actor):
        account = self.user(actor)
        if not account or account.get("role") != "admin":
            return False, "Only an administrator can reset system settings."
        self.data["settings"] = dict(DEFAULT_SYSTEM_SETTINGS)
        self.save()
        return True, "System branding has been reset to defaults."

    def services(self):
        return [dict(service) for service in self.data["services"]]

    def service(self, service_id):
        for service in self.data["services"]:
            if str(service.get("service_id")) == str(service_id):
                return dict(service)
        return None

    def service_by_name(self, name):
        needle = str(name or "").strip().lower()
        for service in self.data["services"]:
            if str(service.get("name") or "").strip().lower() == needle:
                return dict(service)
        return None

    def next_service_id(self):
        numbers = []
        for record in self.data["services"]:
            suffix = str(record.get("service_id") or "").split("-")[-1]
            if suffix.isdigit():
                numbers.append(int(suffix))
        return f"SRV-{(max(numbers) if numbers else 0) + 1:03d}"

    def save_service(self, actor, service_id=None, name="", description="",
                     prices=None, available=True):
        account = self.user(actor)
        if not account or account.get("role") != "admin":
            return False, "Only an administrator can manage services and pricing."
        name = " ".join(str(name or "").split())
        description = " ".join(str(description or "").split())
        if len(name) < 2:
            return False, "Enter a service name with at least 2 characters."
        duplicate = self.service_by_name(name)
        if duplicate and str(duplicate.get("service_id")) != str(service_id):
            return False, "A service with that name already exists."
        cleaned_prices = {}
        prices = prices or {}
        supported_prices = SERVICE_PRICE_DEFAULTS.get(
            name, {vehicle: 0.0 for vehicle in VEHICLES})
        for vehicle in VEHICLES:
            if vehicle not in supported_prices:
                continue
            try:
                value = round(float(prices.get(vehicle)), 2)
            except (TypeError, ValueError):
                return False, f"Enter a valid price for {vehicle}."
            if value <= 0:
                return False, f"The {vehicle} price must be greater than zero."
            if value > 1_000_000:
                return False, f"The {vehicle} price is too large."
            cleaned_prices[vehicle] = value
        target = None
        for service in self.data["services"]:
            if str(service.get("service_id")) == str(service_id):
                target = service
                break
        if service_id and target is None:
            return False, "That service could not be found."
        if target is None:
            target = {
                "service_id": self.next_service_id(),
                "name": name,
                "description": description,
                "prices": cleaned_prices,
                "available": bool(available),
            }
            self.data["services"].append(target)
            message = f"Service {target['service_id']} created."
        else:
            if (target.get("available") and not available
                    and sum(1 for service in self.data["services"]
                            if service.get("available")) <= 1):
                return False, "At least one service must remain available for booking."
            target["name"] = name
            target["description"] = description
            target["prices"] = cleaned_prices
            target["available"] = bool(available)
            message = f"Service {target['service_id']} updated."
        self.save()
        self.apply_service_catalog()
        return True, message

    def set_service_availability(self, actor, service_id, available):
        account = self.user(actor)
        if not account or account.get("role") != "admin":
            return False, "Only an administrator can manage services and pricing."
        target = None
        for service in self.data["services"]:
            if str(service.get("service_id")) == str(service_id):
                target = service
                break
        if target is None:
            return False, "That service could not be found."
        if not available and sum(1 for service in self.data["services"]
                                 if service.get("available")) <= 1:
            return False, "At least one service must remain available for booking."
        target["available"] = bool(available)
        self.save()
        self.apply_service_catalog()
        return True, f"{target['name']} is now {'available' if available else 'unavailable'}."

    def apply_service_catalog(self):
        """Expose available services with only their supported vehicle prices."""
        PRICES.clear()
        for service in self.data["services"]:
            name = service.get("name")
            if not name:
                continue
            supported_vehicles = SERVICE_PRICE_DEFAULTS.get(name, {})
            stored_prices = service.get("prices", {})
            PRICES[name] = {
                vehicle: float(stored_prices.get(vehicle, default_price))
                for vehicle, default_price in supported_vehicles.items()
            }
        SERVICES[:] = [service["name"] for service in self.data["services"]
                       if service.get("available") and service.get("name") in PRICES]

    def _migrate_bank_transfer_fields(self):
        """Convert legacy bank-specific payment/refund method values to Bank Transfer."""
        changed = False
        for payment in self.data["payments"]:
            if str(payment.get("method") or "").strip().upper() == "AUB":
                payment["method"] = "Bank Transfer"
                changed = True
        for refund in self.data["refunds"]:
            if str(refund.get("refund_method") or "").strip().upper() == "AUB":
                refund["refund_method"] = "Bank Transfer"
                if not refund.get("bank_name"):
                    refund["bank_name"] = "AUB"
                changed = True
        for user in self.data["users"]:
            if str(user.get("refund_method") or "").strip().upper() == "AUB":
                user["refund_method"] = "Bank Transfer"
                if not user.get("refund_bank_name"):
                    user["refund_bank_name"] = "AUB"
                changed = True
        if changed:
            self.save()

    def save(self):
        """Write the records back to disk atomically so a crash cannot corrupt the file."""
        temp = DATA_FILE.with_name(DATA_FILE.name + ".tmp")
        try:
            temp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
            os.replace(temp, DATA_FILE)
        except OSError:
            pass

    def seed_accounts(self):
        seeds = [
            ("admin", "Admin123!", "WASHAPP Administrator", "N/A", "admin"),
            ("demo", "demo123", "Demo Customer", "09000000000", "customer"),
        ]
        changed = False
        for username, password, full_name, contact, role in seeds:
            if not self.user(username):
                self.data["users"].append({
                    "username": username,
                    "password_hash": hash_password(password),
                    "full_name": full_name,
                    "contact": contact,
                    "email": "",
                    "role": role,
                    "status": "Active",
                    "created_at": business_now().isoformat(timespec="seconds"),
                })
                changed = True
        if changed:
            self.save()

    def user(self, username):
        for record in self.data["users"]:
            if record["username"] == username:
                return dict(record)
        return None

    def authenticate(self, username, password):
        user = self.user(username)
        if not user or not verify_password(password, user["password_hash"]):
            return None
        if str(user.get("status") or "Active") != "Active":
            return None
        if user["password_hash"] == password:
            self.migrate_legacy_password(username, password)
            user = self.user(username)
        return user

    def admin_usernames(self):
        return [record["username"] for record in self.data["users"]
                if record.get("role") == "admin"
                and str(record.get("status") or "Active") == "Active"]

    def operational_account(self, username):
        account = self.user(username)
        if not account or account.get("role") not in ("admin", "staff"):
            return None
        if str(account.get("status") or "Active") != "Active":
            return None
        return account

    def account_id(self, username):
        return str(username)

    def users_for_management(self, role="All", status="All", query=""):
        rows = []
        needle = str(query or "").strip().lower()
        for record in self.data["users"]:
            if role != "All" and record.get("role") != role:
                continue
            current_status = record.get("status") or "Active"
            if status != "All" and current_status != status:
                continue
            searchable = " ".join(str(record.get(key) or "") for key in
                                  ("username", "full_name", "contact", "email",
                                   "role", "status")).lower()
            if needle and needle not in searchable:
                continue
            row = dict(record)
            row.pop("password_hash", None)
            row["status"] = current_status
            row["email"] = record.get("email") or ""
            rows.append(row)
        rows.sort(key=lambda row: (row.get("role") != "admin",
                                   str(row.get("created_at") or ""),
                                   str(row.get("username") or "")))
        return rows

    def create_user(self, actor, username, password, full_name, contact,
                    email="", role="customer"):
        account = self.user(actor)
        if not account or account.get("role") != "admin":
            return False, "Only an administrator can create accounts."
        username = str(username or "").strip()
        full_name = " ".join(str(full_name or "").split())
        contact = " ".join(str(contact or "").split())
        email = str(email or "").strip()
        role = str(role or "customer").strip().lower()
        if len(username) < 3 or any(ch.isspace() for ch in username):
            return False, "Username must be at least 3 characters and contain no spaces."
        if self.user(username):
            return False, "That username already exists."
        if len(full_name) < 2:
            return False, "Enter the account holder's full name."
        if len(password) < 6:
            return False, "Use a password with at least 6 characters."
        if role not in ACCOUNT_ROLES:
            return False, "Select a valid account role."
        if email and ("@" not in email or "." not in email.split("@")[-1]):
            return False, "Enter a valid email address or leave it blank."
        self.data["users"].append({
            "username": username,
            "password_hash": hash_password(password),
            "full_name": full_name,
            "contact": contact,
            "email": email,
            "role": role,
            "status": "Active",
            "created_at": business_now().isoformat(timespec="seconds"),
        })
        self.save()
        return True, f"Account {username} created as {role}."

    def update_user_account(self, actor, username, full_name, contact, email,
                            role, status):
        acting = self.user(actor)
        if not acting or acting.get("role") != "admin":
            return False, "Only an administrator can edit accounts."
        target = None
        for record in self.data["users"]:
            if record.get("username") == username:
                target = record
                break
        if target is None:
            return False, "That account could not be found."
        full_name = " ".join(str(full_name or "").split())
        contact = " ".join(str(contact or "").split())
        email = str(email or "").strip()
        role = str(role or target.get("role") or "customer").strip().lower()
        status = str(status or target.get("status") or "Active").strip()
        if len(full_name) < 2:
            return False, "Enter the account holder's full name."
        if role not in ACCOUNT_ROLES or status not in ACCOUNT_STATUSES:
            return False, "Select a valid role and account status."
        if email and ("@" not in email or "." not in email.split("@")[-1]):
            return False, "Enter a valid email address or leave it blank."
        if target.get("username") == actor and status != "Active":
            return False, "You cannot deactivate your own signed-in account."
        if target.get("role") == "admin" and role != "admin":
            if sum(1 for record in self.data["users"]
                   if record.get("role") == "admin"
                   and str(record.get("status") or "Active") == "Active") <= 1:
                return False, "The last active administrator must retain the administrator role."
        if target.get("role") == "admin" and status != "Active":
            if sum(1 for record in self.data["users"]
                   if record.get("role") == "admin"
                   and str(record.get("status") or "Active") == "Active") <= 1:
                return False, "The last active administrator cannot be deactivated."
        target["full_name"] = full_name
        target["contact"] = contact
        target["email"] = email
        target["role"] = role
        target["status"] = status
        self.save()
        return True, f"Account {username} updated."

    def set_user_status(self, actor, username, status):
        target = self.user(username)
        if not target:
            return False, "That account could not be found."
        return self.update_user_account(
            actor, username, target.get("full_name"), target.get("contact"),
            target.get("email"), target.get("role"), status)

    def reset_user_password(self, actor, username, new_password):
        acting = self.user(actor)
        if not acting or acting.get("role") != "admin":
            return False, "Only an administrator can reset passwords."
        target = self.user(username)
        if not target:
            return False, "That account could not be found."
        if len(new_password) < 6:
            return False, "Use a new password with at least 6 characters."
        for record in self.data["users"]:
            if record.get("username") == username:
                record["password_hash"] = hash_password(new_password)
                break
        self.save()
        return True, f"Password reset for {username}."

    def register(self, username, password, full_name, contact,
                 refund_method="", refund_account_name="", refund_account_number="",
                 refund_bank_name=""):
        refund_method = str(refund_method or "").strip()
        refund_account_name = " ".join(str(refund_account_name or "").split())
        refund_account_number = str(refund_account_number or "").strip()
        refund_bank_name = (" ".join(str(refund_bank_name or "").split())
                            if refund_method == "Bank Transfer" else "")
        # Refund destination details are optional at registration and are managed
        # separately by the refund workflow. Validate only when any were supplied.
        if any((refund_method, refund_account_name, refund_account_number, refund_bank_name)):
            problem = refund_account_error(
                refund_method, refund_account_name, refund_account_number, refund_bank_name)
            if problem:
                return False, problem
        self.data["users"].append({
            "username": username,
            "password_hash": hash_password(password),
            "full_name": full_name,
            "contact": contact,
            "email": "",
            "role": "customer",
            "status": "Active",
            "created_at": business_now().isoformat(timespec="seconds"),
            "refund_method": refund_method,
            "refund_account_name": refund_account_name,
            "refund_account_number": refund_account_number,
            "refund_bank_name": refund_bank_name,
        })
        self.save()
        return True, "Account created."

    def migrate_legacy_password(self, username, password):
        for record in self.data["users"]:
            if record["username"] == username:
                record["password_hash"] = hash_password(password)
                break
        self.save()

    def change_password(self, username, current_password, new_password):
        """Verify and update only the authenticated user's password hash."""
        user = self.user(username)
        if not user or not verify_password(current_password, user["password_hash"]):
            return False, "Your current password is incorrect."
        if len(new_password) < 6:
            return False, "Use a new password with at least 6 characters."
        if verify_password(new_password, user["password_hash"]):
            return False, "Your new password must be different from your current password."
        for record in self.data["users"]:
            if record["username"] == username:
                record["password_hash"] = hash_password(new_password)
                break
        self.save()
        return True, "Your password has been changed successfully."

    def update_user_refund_details(self, username, method, holder, account_number,
                                   bank_name=""):
        """Save a customer's preferred refund destination on their own account."""
        method = str(method or "").strip()
        holder = " ".join(str(holder or "").split())
        account_number = str(account_number or "").strip()
        bank_name = " ".join(str(bank_name or "").split()) if method == "Bank Transfer" else ""
        problem = refund_account_error(method, holder, account_number, bank_name)
        if problem:
            return False, problem
        user_record = None
        for record in self.data["users"]:
            if record.get("username") == username:
                user_record = record
                break
        if not user_record or user_record.get("role") != "customer":
            return False, "That customer account could not be found."
        user_record["refund_method"] = method
        user_record["refund_account_name"] = holder
        user_record["refund_account_number"] = account_number
        user_record["refund_bank_name"] = bank_name
        now = business_now().isoformat(timespec="seconds")
        for refund in self.data["refunds"]:
            if (refund.get("username") == username
                    and refund.get("status") != "Completed"):
                refund["refund_method"] = method
                refund["account_holder"] = holder
                refund["account_number"] = account_number
                refund["bank_name"] = bank_name
                refund["updated_at"] = now
        self.save()
        return True, "Your payment and refund details have been updated."

    def next_notification_id(self):
        numbers = []
        for record in self.data["notifications"]:
            suffix = str(record.get("notification_id") or "").split("-")[-1]
            if suffix.isdigit():
                numbers.append(int(suffix))
        return f"NOTIF-{(max(numbers) if numbers else 0) + 1:04d}"

    def add_notification(self, username, title, message, related_type=None,
                         related_id=None, event_key=None):
        """Store a private notification and deduplicate repeated operational alerts."""
        if not username:
            return None
        if event_key:
            for existing in self.data["notifications"]:
                if (existing.get("username") == username
                        and existing.get("event_key") == event_key):
                    return dict(existing)
        record = {
            "notification_id": self.next_notification_id(),
            "username": username,
            "title": title,
            "message": message,
            "related_type": related_type,
            "related_id": related_id,
            "event_key": event_key,
            "created_at": business_now().isoformat(timespec="seconds"),
            "read_at": None,
        }
        self.data["notifications"].append(record)
        self.save()
        return dict(record)

    def notify_admins(self, title, message, related_type=None, related_id=None,
                      event_key=None):
        created = []
        for username in self.admin_usernames():
            record = self.add_notification(
                username, title, message, related_type=related_type,
                related_id=related_id, event_key=event_key)
            if record:
                created.append(record)
        return created

    def notifications_for_user(self, username, limit=None):
        rows = [dict(r) for r in self.data["notifications"]
                if r.get("username") == username]
        rows.sort(key=lambda r: (str(r.get("created_at") or ""),
                                 str(r.get("notification_id") or "")), reverse=True)
        return rows[:limit] if limit else rows

    def unread_notification_count(self, username):
        return sum(1 for record in self.data["notifications"]
                   if record.get("username") == username and not record.get("read_at"))

    def sync_admin_notifications(self):
        """Backfill actionable admin alerts once, using event keys to avoid duplicates."""
        for reservation in self.data["reservations"]:
            if reservation.get("status") != "Pending":
                continue
            rid = reservation.get("reservation_id")
            self.notify_admins(
                "Booking Awaiting Approval",
                f"Reservation {rid} for {reservation.get('customer')} is awaiting approval.",
                related_type="reservation", related_id=rid,
                event_key=f"booking_pending:{rid}",
            )
        for payment in self.data["payments"]:
            if payment.get("verification_status") != "Unverified":
                continue
            reservation = self.get_reservation(payment.get("reservation_id"))
            customer = reservation.get("customer") if reservation else "Customer"
            self.notify_admins(
                "Payment Awaiting Verification",
                (f"Payment PAY-{int(payment.get('payment_id', 0)):03d} from {customer} "
                 f"for {money(payment.get('amount_submitted') or 0)} is awaiting verification."),
                related_type="payment", related_id=payment.get("payment_id"),
                event_key=f"payment_pending:{payment.get('payment_id')}",
            )
        for refund in self.data["refunds"]:
            if refund.get("status") not in ("Pending", "Processing"):
                continue
            self.notify_admins(
                "Refund Request Submitted",
                (f"Refund {refund.get('refund_id')} for reservation "
                 f"{refund.get('reservation_id')} is {refund.get('status', 'Pending').lower()}."),
                related_type="refund", related_id=refund.get("refund_id"),
                event_key=f"refund_pending:{refund.get('refund_id')}",
            )

    def reservations_history(self, username=None, start_date=None, end_date=None):
        """Return enriched booking history filtered by the actual booking date."""
        start = start_date if isinstance(start_date, date) else None
        end = end_date if isinstance(end_date, date) else None
        result = []
        for record in self.data["reservations"]:
            if username and record.get("username") != username:
                continue
            try:
                booked = datetime.strptime(str(record.get("booking_date") or ""),
                                           "%m/%d/%Y").date()
            except ValueError:
                continue
            if start and booked < start:
                continue
            if end and booked > end:
                continue
            row = dict(record)
            label, confirmed, pending, remaining = self.payment_status(record["reservation_id"])
            row["verified_paid"] = confirmed
            row["pending_paid"] = pending
            row["remaining_balance"] = remaining
            row["payment_status"] = label
            row["_booking_sort"] = (booked, str(record.get("booking_time") or ""),
                                    str(record.get("reservation_id") or ""))
            result.append(row)
        result.sort(key=lambda row: row["_booking_sort"], reverse=True)
        return result

    def _persist_data_transactionally(self, new_data, make_backup=False):
        """Persist a complete candidate JSON document before replacing in-memory state."""
        temp = DATA_FILE.with_name(DATA_FILE.name + ".delete.tmp")
        backup = DATA_FILE.with_name(DATA_FILE.name + f".backup_{business_now().strftime('%Y%m%d_%H%M%S')}")
        try:
            if make_backup and DATA_FILE.exists():
                shutil.copy2(DATA_FILE, backup)
            temp.write_text(json.dumps(new_data, indent=2), encoding="utf-8")
            # Verify the serialized file can be read before the atomic replace.
            json.loads(temp.read_text(encoding="utf-8"))
            os.replace(temp, DATA_FILE)
        except Exception:
            try:
                if temp.exists():
                    temp.unlink()
            except OSError:
                pass
            raise
        self.data = new_data
        self.apply_service_catalog()

    @staticmethod
    def _booking_linked_notifications(notifications, reservation_ids, payment_ids, refund_ids):
        reservation_ids = {str(x) for x in reservation_ids}
        payment_ids = {str(x) for x in payment_ids}
        refund_ids = {str(x) for x in refund_ids}
        kept = []
        for item in notifications:
            kind = str(item.get("related_type") or "").lower()
            rid = str(item.get("related_id"))
            linked = ((kind in ("reservation", "booking") and rid in reservation_ids)
                      or (kind == "payment" and rid in payment_ids)
                      or (kind == "refund" and rid in refund_ids))
            # Legacy notification event keys can link records even when related_type is absent.
            event = str(item.get("event_key") or "")
            if not linked and any(token in event for token in
                                  [f"booking_pending:{x}" for x in reservation_ids] +
                                  [f"booking_status:{x}:" for x in reservation_ids] +
                                  [f"cancellation_request:{x}" for x in reservation_ids] +
                                  [f"cancellation_requested:{x}" for x in reservation_ids] +
                                  [f"payment_pending:{x}" for x in payment_ids] +
                                  [f"refund_pending:{x}" for x in refund_ids]):
                linked = True
            if not linked:
                kept.append(item)
        return kept

    def _delete_booking_records(self, actor, reservation_ids, clear_all=False):
        account = self.user(actor)
        if not account or account.get("role") != "admin" or str(account.get("status") or "Active") != "Active":
            return False, "Only an active administrator can delete booking history records."
        current = self.data
        selected = {str(value) for value in reservation_ids if str(value).strip()}
        existing = {str(row.get("reservation_id")) for row in current.get("reservations", [])}
        if clear_all:
            selected = set(existing)
        if not selected:
            return False, "Please select at least one booking record to delete."
        missing = selected - existing
        if missing:
            return False, "One or more selected bookings could not be found. Refresh the history and try again."
        reservations = [r for r in current.get("reservations", []) if str(r.get("reservation_id")) in selected]
        payments = [p for p in current.get("payments", []) if str(p.get("reservation_id")) in selected]
        payment_ids = {str(p.get("payment_id")) for p in payments}
        refunds = [r for r in current.get("refunds", []) if str(r.get("reservation_id")) in selected]
        refund_ids = {str(r.get("refund_id")) for r in refunds}
        old_paths = [p.get("reference_image") for p in payments if p.get("reference_image")]
        new_data = json.loads(json.dumps(current))
        new_data["reservations"] = [r for r in current.get("reservations", []) if str(r.get("reservation_id")) not in selected]
        new_data["payments"] = [p for p in current.get("payments", []) if str(p.get("reservation_id")) not in selected]
        new_data["refunds"] = [r for r in current.get("refunds", []) if str(r.get("reservation_id")) not in selected]
        new_data["notifications"] = self._booking_linked_notifications(
            current.get("notifications", []), selected, payment_ids, refund_ids)
        try:
            self._persist_data_transactionally(new_data, make_backup=clear_all)
        except Exception as exc:
            return False, f"Could not save deletion; no changes were committed: {exc}"
        # Delete a payment image only after the JSON update succeeds and only if no
        # remaining payment record refers to the same path.
        referenced = {str(p.get("reference_image")) for p in self.data.get("payments", []) if p.get("reference_image")}
        for value in old_paths:
            if not value or str(value) in referenced:
                continue
            path = Path(str(value))
            if not path.is_absolute():
                path = APP_DIR / path
            try:
                if path.is_file() and (path.resolve().is_relative_to(PAYMENT_IMAGE_DIR.resolve())):
                    path.unlink()
            except (OSError, ValueError, AttributeError):
                pass
        return True, f"{len(reservations)} booking record(s) deleted successfully."

    def delete_booking_history(self, actor, reservation_ids):
        """Delete selected bookings and their linked financial and notification records."""
        return self._delete_booking_records(actor, reservation_ids, clear_all=False)

    def clear_all_booking_data(self, actor):
        """Back up and remove all booking-related records without touching accounts/settings/services."""
        all_ids = [str(row.get("reservation_id")) for row in self.data.get("reservations", [])]
        if not all_ids:
            account = self.user(actor)
            if not account or account.get("role") != "admin" or str(account.get("status") or "Active") != "Active":
                return False, "Only an active administrator can clear booking data."
            # Still clean dangling financial/booking notifications safely when no reservation remains.
            new_data = json.loads(json.dumps(self.data))
            payment_ids = {str(p.get("payment_id")) for p in new_data.get("payments", [])}
            refund_ids = {str(r.get("refund_id")) for r in new_data.get("refunds", [])}
            paths = [p.get("reference_image") for p in new_data.get("payments", []) if p.get("reference_image")]
            new_data["payments"] = []
            new_data["refunds"] = []
            new_data["notifications"] = self._booking_linked_notifications(new_data.get("notifications", []), set(), payment_ids, refund_ids)
            try:
                self._persist_data_transactionally(new_data, make_backup=True)
            except Exception as exc:
                return False, f"Could not save clear-all operation; no changes were committed: {exc}"
            for value in paths:
                path = Path(str(value or ""))
                if value and not path.is_absolute(): path = APP_DIR / path
                try:
                    if value and path.is_file() and path.resolve().is_relative_to(PAYMENT_IMAGE_DIR.resolve()): path.unlink()
                except (OSError, ValueError, AttributeError): pass
            return True, "0 booking record(s) deleted successfully."
        return self._delete_booking_records(actor, all_ids, clear_all=True)

    def history_summary(self, rows, customer=False):
        completed = sum(1 for row in rows if row.get("status") == "Completed")
        cancelled = sum(1 for row in rows if row.get("status") == "Cancelled")
        rejected = sum(1 for row in rows if row.get("status") == "Rejected")
        value = round(sum(float(row.get("total") or 0) for row in rows), 2)
        paid = round(sum(float(row.get("verified_paid") or 0) for row in rows), 2)
        return {
            "count": len(rows),
            "completed": completed,
            "cancelled": cancelled,
            "rejected": rejected,
            "value": value,
            "paid": paid,
            "customer": customer,
        }

    def mark_notification_read(self, notification_id, username):
        for record in self.data["notifications"]:
            if (str(record.get("notification_id")) == str(notification_id)
                    and record.get("username") == username):
                if not record.get("read_at"):
                    record["read_at"] = business_now().isoformat(timespec="seconds")
                    self.save()
                return True
        return False

    def mark_all_notifications_read(self, username):
        now = business_now().isoformat(timespec="seconds")
        changed = False
        for record in self.data["notifications"]:
            if record.get("username") == username and not record.get("read_at"):
                record["read_at"] = now
                changed = True
        if changed:
            self.save()
        return changed

    def reservations_for_user(self, username):
        rows = [r for r in self.data["reservations"] if r["username"] == username]
        return [dict(r) for r in self._newest_first(rows)]

    def all_reservations(self, status=None):
        rows = list(self.data["reservations"])
        if status and status != "All":
            rows = [r for r in rows if r["status"] == status]
        result = []
        for record in self._newest_first(rows):
            row = dict(record)
            label, confirmed, pending, remaining = self.payment_status(record["reservation_id"])
            row["verified_paid"] = confirmed
            row["remaining_balance"] = remaining
            row["payment_status"] = label
            row["latest_payment_status"] = label
            result.append(row)
        return result

    def get_reservation(self, reservation_id):
        for record in self.data["reservations"]:
            if record["reservation_id"] == reservation_id:
                return dict(record)
        return None

    def bay_available(self, bay, booking_date, booking_time, exclude_id=None):
        if not slot_is_future(booking_date, booking_time):
            return False
        for record in self.data["reservations"]:
            if record["reservation_id"] == exclude_id:
                continue
            if (record["bay"] == bay and record["booking_date"] == booking_date
                    and record["booking_time"] == booking_time
                    and record["status"] in ("Pending", "Approved")):
                return False
        return True

    def next_reservation_id(self):
        numbers = []
        for record in self.data["reservations"]:
            suffix = str(record["reservation_id"]).split("-")[-1]
            if suffix.isdigit():
                numbers.append(int(suffix))
        return f"RES-{(max(numbers) if numbers else 0) + 1:04d}"

    def create_reservation(self, values):
        """Validate the vehicle/service pair and snapshot its current catalog price."""
        if not slot_is_future(values["date"], values["time"]):
            raise ValueError("This reservation time has already passed. Please select a future time slot.")
        vehicle_name = str(values.get("vehicle") or "").strip()
        service = str(values.get("service") or "").strip()
        if vehicle_name not in VEHICLES:
            raise ValueError("Please select a valid vehicle type.")
        if service not in PRICES or vehicle_name not in PRICES.get(service, {}):
            raise ValueError("The selected service is not available for this vehicle type.")
        # Do not trust a stale UI total: the catalog price is authoritative for new bookings.
        authoritative_total = round(float(PRICES[service][vehicle_name]), 2)
        if not self.bay_available(values["bay"], values["date"], values["time"]):
            raise ValueError(f"{values['bay']} is no longer available for this date and time. Please choose another bay.")
        reservation_id = values.get("reservation_id") or self.next_reservation_id()
        if any(r["reservation_id"] == reservation_id for r in self.data["reservations"]):
            raise ValueError("This reservation already exists. Please submit your request again.")
        self.data["reservations"].append({
            "reservation_id": reservation_id,
            "username": values["username"],
            "customer": values["customer"],
            "contact": values["contact"],
            "vehicle": vehicle_name,
            "service": service,
            "booking_date": values["date"],
            "booking_time": values["time"],
            "bay": values["bay"],
            "total": authoritative_total,
            "status": "Pending",
            "created_at": business_now().isoformat(timespec="seconds"),
            "reviewed_at": None,
            "reviewed_by": None,
        })
        self.save()
        self.notify_admins(
            "New Booking Submitted",
            (f"{values['customer']} booked {service} for "
             f"{values['date']} at {values['time']} ({values['bay']})."),
            related_type="reservation", related_id=reservation_id,
            event_key=f"booking_pending:{reservation_id}",
        )
        return reservation_id

    def set_booking_status(self, reservation_id, status, admin, actor_role=None, customer_request=False, cancellation_reason=None, cancellation_reason_category=None, rejection_reason=None, rejection_reason_category=None):
        reservation_record = self.get_reservation(reservation_id)
        if not reservation_record:
            raise ValueError("Reservation not found.")
        
        actor_account = self.user(admin)
        is_operational = self.operational_account(admin) is not None
        is_customer_actor = actor_account and actor_account.get("role") == "customer"
        if actor_role:
            is_customer_actor = actor_role == "customer" or is_customer_actor
        
        # Allow customers to cancel their own eligible reservations
        if customer_request or (is_customer_actor and status == "Cancelled"):
            if not is_customer_actor:
                raise PermissionError("Only the customer who created this reservation can cancel it.")
            if reservation_record.get("username") != admin:
                raise PermissionError("You can only cancel your own reservations.")
            current_status = reservation_record.get("status")
            if current_status in ("Completed", "Rejected", "Cancelled", "Cancellation Requested"):
                raise PermissionError(f"This reservation cannot be cancelled because its status is {current_status}.")
            if current_status not in ("Pending", "Approved"):
                raise PermissionError("This reservation is not eligible for cancellation.")
            
            # Check if there are verified/paid payments
            confirmed_paid = self._verified_total(reservation_id)
            pending_paid = self._pending_total(reservation_id)
            total_paid_submitted = confirmed_paid + pending_paid
            
            if total_paid_submitted > 0.005:  # Has payments/submissions
                # Submit cancellation request for admin review
                for record in self.data["reservations"]:
                    if record["reservation_id"] == reservation_id:
                        record["cancellation_requested_from_status"] = current_status
                        record["status"] = "Cancellation Requested"
                        record["cancellation_requested_at"] = business_now().isoformat(timespec="seconds")
                        record["cancellation_requested_by"] = admin
                        reservation_record = dict(record)
                        break
                self.save()
                self.notify_admins(
                    "Cancellation Request Submitted",
                    f"Customer {reservation_record.get('customer')} has requested cancellation for reservation {reservation_id} with existing payments ({money(total_paid_submitted)} submitted).",
                    related_type="reservation", related_id=reservation_id,
                    event_key=f"cancellation_request:{reservation_id}",
                )
                self.add_notification(
                    reservation_record.get("username"),
                    "Cancellation Request Submitted",
                    f"Your cancellation request for reservation {reservation_id} has been submitted and is awaiting admin review.",
                    related_type="reservation", related_id=reservation_id,
                    event_key=f"cancellation_requested:{reservation_id}",
                )
                return
            else:
                # No payments, can cancel directly
                status = "Cancelled"
        elif not is_operational:
            raise PermissionError("Only active staff or administrators can update bookings.")
        
        reservation = None
        for record in self.data["reservations"]:
            if record["reservation_id"] == reservation_id:
                record["status"] = status
                record["reviewed_at"] = business_now().isoformat(timespec="seconds")
                record["reviewed_by"] = admin
                if status == "Cancelled":
                    record["cancelled_at"] = business_now().isoformat(timespec="seconds")
                    record["cancelled_by"] = admin
                    if cancellation_reason:
                        record["cancellation_reason"] = str(cancellation_reason).strip()
                        record["cancellation_reason_category"] = str(cancellation_reason_category or "Others").strip()
                if status == "Rejected":
                    record["rejected_at"] = business_now().isoformat(timespec="seconds")
                    record["rejected_by"] = admin
                    if rejection_reason:
                        record["rejection_reason"] = str(rejection_reason).strip()
                        record["rejection_reason_category"] = str(rejection_reason_category or "Other").strip()
                reservation = dict(record)
                break
        self.save()
        # Release bay if cancelled/finalized and no other active reservation
        if status == "Cancelled":
            try:
                # Bay is considered free - other reservations will check against active ones; our bay_available already excludes Pending/Approved
                pass
            except Exception:
                pass
        if reservation:
            messages = {
                "Approved": ("Booking Approved",
                             f"Your booking {reservation_id} for {reservation['booking_date']} "
                             f"at {reservation['booking_time']} has been approved."),
                "Rejected": ("Reservation Rejected",
                             f"Your reservation {reservation_id} has been rejected.\n\nReason: "
                             f"{reservation.get('rejection_reason') or 'The reservation was rejected by the administrator.'}\n\n"
                             "Please contact the carwash administrator if you need further assistance."),
                "Completed": ("Booking Completed",
                              f"Your booking {reservation_id} has been marked completed."),
                "Cancelled": ("Reservation Cancelled",
                              (f"Your reservation {reservation_id} has been cancelled.\n\nReason: "
                               f"{reservation.get('cancellation_reason') or 'The reservation was cancelled by the administrator.'}")),
                "Pending": ("Booking Returned to Pending",
                            f"Your booking {reservation_id} is awaiting approval again."),
                "Cancellation Requested": ("Cancellation Request Submitted",
                                           f"Your cancellation request for booking {reservation_id} is awaiting admin review."),
            }
            if status in messages:
                title, message = messages[status]
                self.add_notification(
                    reservation.get("username"), title, message,
                    related_type="reservation", related_id=reservation_id,
                    event_key=f"booking_status:{reservation_id}:{status}",
                )
        if status == "Rejected":
            self.ensure_refund_request(reservation_id)

    def add_payment(self, reservation_id, method, due, amount, reference=None, reference_image=None,
                    payment_option=None, policy_accepted=False):
        """Store a submitted payment as unverified; only an admin can confirm it."""
        if not reference_image:
            raise ValueError("Please upload a payment reference image before submitting.")
        ok, message = validate_payment_image(reference_image)
        if not ok:
            raise ValueError(message)
        reservation = self.get_reservation(reservation_id)
        if not reservation:
            raise ValueError("That reservation could not be found.")
        if method not in PAYMENT_METHODS:
            raise ValueError("Please select GCash or Bank Transfer as your payment method.")
        if not policy_accepted:
            raise ValueError("You must confirm the payment and cancellation policy before continuing.")
        eligibility = self.payment_eligibility(reservation_id)
        if not eligibility or eligibility["outstanding"] <= 0:
            raise ValueError("This reservation is already fully paid. No further payment can be submitted.")
        try:
            amount = round(float(amount), 2)
        except (TypeError, ValueError):
            raise ValueError("Enter a valid numeric payment amount.")
        if amount <= 0:
            raise ValueError("Payment amount must be greater than zero.")
        if amount > eligibility["outstanding"] + 0.005:
            raise ValueError(
                f"Payment cannot exceed the outstanding balance of "
                f"{money(eligibility['outstanding'])}."
            )
        if payment_option:
            option = str(payment_option).strip().lower()
            if "downpayment" in option or "30%" in option:
                if not eligibility["downpayment_available"]:
                    raise ValueError(eligibility["downpayment_message"] or DOWNPAYMENT_COMPLETED_MESSAGE)
                expected = round(max(0.0, float(eligibility.get("downpayment") or 0) - float(eligibility.get("confirmed") or 0)), 2)
                if abs(amount - expected) > 0.005:
                    raise ValueError(f"The amount for Downpayment (30%) must be {money(expected)}.")
                if amount > expected + 0.005 or amount > float(eligibility["outstanding"]) + 0.005:
                    raise ValueError("The submitted amount exceeds the required amount for the selected payment option.")
            elif "full" in option:
                if not eligibility["full_available"]:
                    raise ValueError(eligibility["full_message"] or "The Full Payment option is no longer available.")
                expected = round(float(eligibility["outstanding"]), 2)
                if abs(amount - expected) > 0.005:
                    raise ValueError(f"The amount for Full Payment must be {money(expected)}.")
            else:
                raise ValueError("Please select a valid payment option.")
        if not reference:
            reference = f"IMG-{business_now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3).upper()}"
        if self.reference_taken(method, reference):
            raise ValueError("A payment with this reference number was already recorded. Duplicate transactions are not allowed.")
        next_id = 1
        for record in self.data["payments"]:
            try:
                next_id = max(next_id, int(record["payment_id"]) + 1)
            except (KeyError, TypeError, ValueError):
                continue
        saved_image = self._store_payment_image(reference_image, next_id, reservation_id)
        self.data["payments"].append({
            "payment_id": next_id,
            "reservation_id": reservation_id,
            "method": method,
            "downpayment_due": eligibility["downpayment"],
            "amount_submitted": amount,
            "reference": reference,
            "reference_image": saved_image,
            "reference_image_name": Path(reference_image).name,
            "verification_status": "Unverified",
            "submitted_at": business_now().isoformat(timespec="seconds"),
            "verified_at": None,
            "verified_by": None,
            "payment_option": payment_option,
            "cancellation_policy_accepted": True,
            "cancellation_policy_accepted_at": business_now().isoformat(timespec="seconds"),
        })
        self.save()
        self.notify_admins(
            "Payment Awaiting Verification",
            (f"{reservation.get('customer')} submitted {money(amount)} for "
             f"reservation {reservation_id} via {method}."),
            related_type="payment", related_id=next_id,
            event_key=f"payment_pending:{next_id}",
        )
        return next_id

    def _store_payment_image(self, source_path, payment_id, reservation_id):
        """Copy the uploaded image into the app data folder so it survives logouts and restarts."""
        source = Path(source_path)
        suffix = source.suffix.lower()
        stamp = business_now().strftime("%Y%m%d%H%M%S")
        target_name = f"PAY-{int(payment_id):03d}-{reservation_id}-{stamp}-{secrets.token_hex(3)}{suffix}"
        target = PAYMENT_IMAGE_DIR / target_name
        try:
            shutil.copyfile(source, target)
        except OSError as exc:
            raise ValueError(f"The payment reference image could not be saved: {exc}") from exc
        try:
            return target.relative_to(APP_DIR).as_posix()
        except ValueError:
            return str(target)

    def payment_image_path(self, record):
        """Resolve the stored image path for a payment record, or None when unavailable."""
        if not isinstance(record, dict):
            return None
        value = record.get("reference_image")
        if not value:
            return None
        path = Path(value)
        if not path.is_absolute():
            path = APP_DIR / path
        return path if path.is_file() else None

    def reference_taken(self, method, reference, exclude_payment_id=None):
        """A transaction reference may only be recorded once per payment method."""
        needle = str(reference).strip().lower()
        if not needle:
            return False
        for record in self.data["payments"]:
            if exclude_payment_id is not None and str(record.get("payment_id")) == str(exclude_payment_id):
                continue
            same_method = str(record.get("method") or "").strip().lower() == str(method).strip().lower()
            same_reference = str(record.get("reference") or "").strip().lower() == needle
            if same_method and same_reference:
                return True
        return False

    def payment_status(self, reservation_id):
        """Return (status label, confirmed paid, awaiting verification, remaining balance)."""
        record = self.get_reservation(reservation_id)
        total = float(record["total"]) if record else 0.0
        confirmed = round(self._verified_total(reservation_id), 2)
        pending = round(self._pending_total(reservation_id), 2)
        remaining = round(max(0.0, total - confirmed), 2)
        return payment_status_label(total, confirmed, pending), confirmed, pending, remaining

    def available_to_pay(self, reservation_id):
        """Amount the customer may still submit (total minus already confirmed payments)."""
        record = self.get_reservation(reservation_id)
        total = float(record["total"]) if record else 0.0
        confirmed = self._verified_total(reservation_id)
        return round(max(0.0, total - confirmed), 2)

    def payment_eligibility(self, reservation_id):
        """Return confirmed balances and the payment options currently allowed."""
        record = self.get_reservation(reservation_id)
        if not record:
            return None
        total = round(float(record.get("total") or 0), 2)
        downpayment = round(total * DOWNPAYMENT_RATE, 2)
        confirmed = round(self._verified_total(reservation_id), 2)
        pending = round(self._pending_total(reservation_id), 2)
        outstanding = round(max(0.0, total - confirmed), 2)
        downpayment_remaining = round(max(0.0, downpayment - confirmed), 2)
        downpayment_available = outstanding > 0.005 and downpayment_remaining > 0.005
        full_available = outstanding > 0.005
        downpayment_exceeded = confirmed > downpayment + 0.005
        downpayment_completed = abs(confirmed - downpayment) <= 0.005
        if downpayment_exceeded:
            downpayment_state = "exceeded"
            downpayment_message = DOWNPAYMENT_EXCEEDED_MESSAGE
        elif downpayment_completed:
            downpayment_state = "completed"
            downpayment_message = DOWNPAYMENT_COMPLETED_MESSAGE
        elif not downpayment_available:
            downpayment_state = "unavailable"
            downpayment_message = ("The 30% payment option is unavailable for this reservation.")
        else:
            downpayment_state = "available"
            downpayment_message = ""
        full_message = ("The Full Payment option is unavailable because the outstanding balance is zero.")
        return {
            "total": total,
            "downpayment": downpayment,
            "downpayment_remaining": downpayment_remaining,
            "confirmed": confirmed,
            "pending": pending,
            "outstanding": outstanding,
            "downpayment_available": downpayment_available,
            "full_available": full_available,
            "downpayment_state": downpayment_state,
            "downpayment_exceeded": downpayment_exceeded,
            "downpayment_completed": downpayment_completed,
            "downpayment_message": downpayment_message,
            "full_message": full_message,
        }

    def payments_for_reservation(self, reservation_id):
        rows = [p for p in self.data["payments"] if p["reservation_id"] == reservation_id]
        rows.sort(key=lambda p: int(p["payment_id"]), reverse=True)
        return [dict(p) for p in rows]

    def latest_payment(self, reservation_id):
        payments = self.payments_for_reservation(reservation_id)
        return payments[0] if payments else None

    def next_refund_id(self):
        numbers = []
        for record in self.data["refunds"]:
            suffix = str(record.get("refund_id") or "").split("-")[-1]
            if suffix.isdigit():
                numbers.append(int(suffix))
        return f"REF-{(max(numbers) if numbers else 0) + 1:04d}"

    def _refund_record(self, refund_id):
        for record in self.data["refunds"]:
            if str(record.get("refund_id")) == str(refund_id):
                return record
        return None

    def refunds_for_reservation(self, reservation_id):
        # JSON data may contain reservation IDs as strings while callers pass integers.
        # Normalize both sides so the admin indicator always finds the correct request.
        target_id = str(reservation_id)
        rows = [dict(r) for r in self.data["refunds"]
                if str(r.get("reservation_id")) == target_id]
        rows.sort(key=lambda r: str(r.get("refund_id") or ""))
        return rows

    def refund_for_reservation(self, reservation_id):
        refunds = self.refunds_for_reservation(reservation_id)
        return refunds[-1] if refunds else None

    def refunds_for_user(self, username):
        rows = [dict(r) for r in self.data["refunds"] if r.get("username") == username]
        rows.sort(key=lambda r: (str(r.get("created_at") or ""), str(r.get("refund_id") or "")),
                  reverse=True)
        return rows

    def cancellation_fee_for_reservation(self, reservation):
        """Apply the fee only when an approved reservation is later cancelled."""
        if not reservation or reservation.get("status") not in ("Cancellation Requested", "Cancelled"):
            return 0.0
        was_approved = (reservation.get("cancellation_requested_from_status") == "Approved"
                        or (not reservation.get("cancellation_requested_from_status")
                            and bool(reservation.get("reviewed_at") and reservation.get("reviewed_by"))))
        if not was_approved:
            return 0.0
        return cancellation_fee(reservation.get("total") or 0)

    def ensure_refund_request(self, reservation_id):
        """Create or update one pending refund request for eligible confirmed payments."""
        reservation = self.get_reservation(reservation_id)
        if not reservation or reservation.get("status") not in (
                "Rejected", "Cancellation Requested", "Cancelled", "Refunded"):
            return None
        verified = [p for p in self.data["payments"]
                    if p.get("reservation_id") == reservation_id
                    and p.get("verification_status") == "Verified"]
        confirmed = round(sum(float(p.get("amount_submitted") or 0) for p in verified), 2)
        existing = self.refund_for_reservation(reservation_id)
        if existing:
            record = self._refund_record(existing.get("refund_id"))
            if not record:
                return None
            if record.get("status") in ("Pending", "Under Review", "Processing"):
                other_refunds = sum(
                    float(ref.get("amount") or 0) for ref in self.data["refunds"]
                    if ref.get("reservation_id") == reservation_id
                    and str(ref.get("refund_id")) != str(record.get("refund_id"))
                    and ref.get("status") != "Cancelled"
                )
                fee = self.cancellation_fee_for_reservation(reservation)
                refundable = round(max(0.0, confirmed - fee - other_refunds), 2)
                payment_ids = sorted({int(p["payment_id"]) for p in verified})
                payment_methods = sorted({str(p.get("method") or "") for p in verified
                                          if p.get("method")})
                changed = False
                if abs(refundable - float(record.get("amount") or 0)) > 0.005:
                    record["amount"] = refundable
                    changed = True
                if record.get("payment_ids") != payment_ids:
                    record["payment_ids"] = payment_ids
                    changed = True
                if record.get("payment_methods") != payment_methods:
                    record["payment_methods"] = payment_methods
                    changed = True
                if abs(float(record.get("confirmed_paid") or 0) - confirmed) > 0.005:
                    record["confirmed_paid"] = confirmed
                    changed = True
                if changed:
                    record["updated_at"] = business_now().isoformat(timespec="seconds")
                    self.save()
            return dict(record)
        if confirmed <= 0.005:
            return None
        other_refunds = sum(
            float(ref.get("amount") or 0) for ref in self.data["refunds"]
            if ref.get("reservation_id") == reservation_id
            and ref.get("status") != "Cancelled"
        )
        fee = self.cancellation_fee_for_reservation(reservation)
        amount = round(max(0.0, confirmed - fee - other_refunds), 2)
        if amount <= 0.005:
            return None
        now = business_now().isoformat(timespec="seconds")
        customer = self.user(reservation.get("username")) or {}
        record = {
            "refund_id": self.next_refund_id(),
            "reservation_id": reservation_id,
            "username": reservation.get("username"),
            "amount": amount,
            "confirmed_paid": confirmed,
            "payment_ids": sorted({int(p["payment_id"]) for p in verified}),
            "payment_methods": sorted({str(p.get("method") or "") for p in verified
                                       if p.get("method")}),
            "refund_method": customer.get("refund_method") or "",
            "account_holder": customer.get("refund_account_name") or "",
            "account_number": customer.get("refund_account_number") or "",
            "bank_name": customer.get("refund_bank_name") or "",
            "status": "Pending",
            "reference": "",
            "refund_date": "",
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
            "processed_by": None,
        }
        self.data["refunds"].append(record)
        self.save()
        self.notify_admins(
            "Refund Request Submitted",
            (f"Refund {record['refund_id']} for reservation {reservation_id} "
             f"needs processing ({money(amount)})."),
            related_type="refund", related_id=record["refund_id"],
            event_key=f"refund_pending:{record['refund_id']}",
        )
        self.add_notification(
            record.get("username"), "Refund Request Created",
            (f"A refund request for reservation {reservation_id} was created for "
             f"{money(amount)}. Please provide or review your refund details."),
            related_type="refund", related_id=record["refund_id"],
            event_key=f"refund_created:{record['refund_id']}",
        )
        return dict(record)

    def submit_customer_refund_request(self, reservation_id, username):
        """Submit one refund request for a customer's cancelled/cancellation-requested booking."""
        reservation = self.get_reservation(reservation_id)
        if not reservation or reservation.get("username") != username:
            return False, "That reservation could not be found or does not belong to your account."
        if reservation.get("status") not in ("Cancellation Requested", "Cancelled"):
            return False, "A refund can only be requested after you have requested cancellation."

        confirmed = round(self._verified_total(reservation_id), 2)
        if confirmed <= 0.005:
            return False, "No confirmed payment was found. You cannot request a refund without a verified payment."

        existing = self.refund_for_reservation(reservation_id)
        if existing and (existing.get("requested_by_customer") or existing.get("request_submitted_at")):
            return False, (f"A refund request already exists for this reservation ({existing.get('status') or 'Pending'}). "
                           "Duplicate refund requests are not allowed.")
        if existing and str(existing.get("status") or "Pending") in ("Completed", "Rejected"):
            return False, f"This refund is already {str(existing.get('status')).lower()} and cannot be requested again."

        # Reuse a previously created pending refund record (for example, one created
        # by an earlier admin workflow) instead of treating it as a customer request.
        # The explicit customer click is what sets requested_by_customer.
        record = existing or self.ensure_refund_request(reservation_id)
        if not record:
            return False, "A refund request could not be created because there is no refundable confirmed payment."

        stored = self._refund_record(record.get("refund_id"))
        if stored is not None:
            stored["requested_by_customer"] = True
            stored["request_submitted_at"] = business_now().isoformat(timespec="seconds")
            if not stored.get("status"):
                stored["status"] = "Pending"
            stored["updated_at"] = stored["request_submitted_at"]
            self.save()
            # Notify admins even when the refund record already existed before the
            # customer explicitly submitted the request.
            self.notify_admins(
                "Customer Refund Requested",
                f"The customer has requested a refund for reservation {reservation_id} ({money(stored.get('amount') or 0)}).",
                related_type="refund", related_id=stored.get("refund_id"),
                event_key=f"customer_refund_requested:{stored.get('refund_id')}"
            )
        return True, (f"Your refund request for reservation {reservation_id} has been submitted. "
                      "It is pending admin review; this does not mean the refund has been paid.")

    def get_refund(self, refund_id):
        record = self._refund_record(refund_id)
        return dict(record) if record else None

    def update_refund_details(self, reservation_id, username, method, holder, account_number,
                              bank_name=""):
        """Save the authenticated customer's refund destination for their rejected booking."""
        reservation = self.get_reservation(reservation_id)
        if not reservation or reservation.get("username") != username:
            return False, "That reservation could not be found."
        if reservation.get("status") not in (
                "Rejected", "Cancellation Requested", "Cancelled"):
            return False, "Refund details are only available for eligible cancelled or rejected bookings."
        refund = self.refund_for_reservation(reservation_id)
        if not refund:
            refund = self.ensure_refund_request(reservation_id)
        if not refund:
            return False, "No confirmed payment is available to refund for this booking."
        if refund.get("status") == "Completed":
            return False, "This refund has already been completed and can no longer be changed."
        method = str(method or "").strip()
        holder = " ".join(str(holder or "").split())
        account_number = str(account_number or "").strip()
        bank_name = " ".join(str(bank_name or "").split()) if method == "Bank Transfer" else ""
        message = refund_account_error(method, holder, account_number, bank_name)
        if message:
            return False, message
        record = self._refund_record(refund.get("refund_id"))
        if not record:
            return False, "The refund request could not be found."
        record["refund_method"] = method
        record["account_holder"] = holder
        record["account_number"] = account_number
        record["bank_name"] = bank_name
        record["updated_at"] = business_now().isoformat(timespec="seconds")
        for user_record in self.data["users"]:
            if user_record.get("username") == username:
                user_record["refund_method"] = method
                user_record["refund_account_name"] = holder
                user_record["refund_account_number"] = account_number
                user_record["refund_bank_name"] = bank_name
                break
        self.save()
        return True, "Your refund details have been saved."

    def update_refund_status(self, refund_id, status, admin, amount=None, refund_date=None,
                             reference=None):
        """Record an admin-processed refund and notify the customer when completed."""
        account = self.user(admin)
        if not account or account.get("role") != "admin":
            return False, "Only an authorized admin can update a refund."
        record = self._refund_record(refund_id)
        if not record:
            return False, "That refund request could not be found."
        status = str(status or "").strip()
        if status not in REFUND_STATUSES:
            return False, "Please select a valid refund status."
        if record.get("status") == "Completed":
            return False, "This refund has already been completed."
        reservation_id = record.get("reservation_id")
        if status == "Completed":
            already_completed = next((ref for ref in self.data["refunds"]
                                      if str(ref.get("reservation_id")) == str(reservation_id)
                                      and str(ref.get("refund_id")) != str(refund_id)
                                      and ref.get("status") == "Completed"), None)
            if already_completed:
                return False, ("A completed refund already exists for this reservation. "
                               "Duplicate refund processing is not allowed.")
        method = record.get("refund_method") or ""
        holder = record.get("account_holder") or ""
        account_number = record.get("account_number") or ""
        bank_name = record.get("bank_name") or ""
        if status in ("Processing", "Completed"):
            if not refund_details_complete(method, holder, account_number, bank_name):
                return False, "The customer has not submitted complete refund account details yet."
        now = business_now().isoformat(timespec="seconds")
        verified = [p for p in self.data["payments"]
                    if p.get("reservation_id") == reservation_id
                    and p.get("verification_status") == "Verified"]
        confirmed = round(sum(float(p.get("amount_submitted") or 0) for p in verified), 2)
        other_completed = sum(
            float(ref.get("amount") or 0) for ref in self.data["refunds"]
            if ref.get("reservation_id") == reservation_id
            and str(ref.get("refund_id")) != str(record.get("refund_id"))
            and ref.get("status") == "Completed"
        )
        reservation = self.get_reservation(reservation_id)
        cancellation_deduction = self.cancellation_fee_for_reservation(reservation)
        refundable = round(max(0.0, confirmed - cancellation_deduction - other_completed), 2)
        if refundable <= 0.005 and status in ("Processing", "Completed"):
            return False, "No confirmed payment amount remains to refund."

        supplied_amount = str(amount).strip() if amount is not None else ""
        if supplied_amount:
            try:
                refund_amount = round(float(supplied_amount), 2)
            except (TypeError, ValueError):
                return False, "Enter a valid refund amount."
            if refund_amount <= 0:
                return False, "The refund amount must be greater than zero."
            if refund_amount > refundable + 0.005:
                return False, f"The refund amount cannot exceed the refundable balance of {money(refundable)}."
            record["amount"] = refund_amount

        supplied_date = str(refund_date).strip() if refund_date is not None else ""
        if supplied_date:
            normalized_date = normalize_refund_date(supplied_date)
            if normalized_date is None:
                return False, "Enter the refund date as MM/DD/YYYY."
            record["refund_date"] = normalized_date
        elif status == "Completed" and not record.get("refund_date"):
            record["refund_date"] = business_now().date().isoformat()

        if reference is not None:
            record["reference"] = str(reference).strip()
        if status == "Completed":
            record["confirmed_paid"] = confirmed
            record["payment_ids"] = sorted({int(p["payment_id"]) for p in verified})
            record["payment_methods"] = sorted({str(p.get("method") or "") for p in verified
                                                if p.get("method")})
            record["completed_at"] = now
        record["status"] = status
        record["processed_by"] = admin
        record["updated_at"] = now
        if status == "Completed":
            record["completed_at"] = now
            # Preserve the payment history; only update the reservation's final status.
            for reservation_record in self.data["reservations"]:
                if str(reservation_record.get("reservation_id")) == str(reservation_id):
                    reservation_record["status"] = "Refunded"
                    reservation_record["reviewed_at"] = now
                    reservation_record["reviewed_by"] = admin
                    reservation_record["refunded_at"] = now
                    reservation_record["refunded_by"] = admin
                    break
        self.save()
        if status == "Completed":
            self.add_notification(
                record.get("username"),
                "Refund Successfully Processed",
                (f"Your refund for reservation {reservation_id} has been successfully "
                 f"processed. The refunded amount is {money(record.get('amount') or 0)}. "
                 "Please allow the applicable processing time for the funds to appear "
                 "in your GCash or bank account, if applicable."),
                related_type="refund",
                related_id=record.get("refund_id"),
                event_key=f"refund_status:{refund_id}:Completed",
            )
        elif status == "Processing":
            self.add_notification(
                record.get("username"), "Refund Processing",
                (f"Your refund of {money(record.get('amount') or 0)} for reservation "
                 f"{reservation_id} is now being processed."),
                related_type="refund", related_id=record.get("refund_id"),
                event_key=f"refund_status:{refund_id}:Processing",
            )
        return True, f"Refund {refund_id} status set to {status}."

    def verify_payment(self, payment_id, status, admin):
        """Only authorized staff or administrators may verify a submitted payment."""
        account = self.operational_account(admin)
        if not account:
            return False, "Only authorized staff or administrators can verify a payment."
        target = None
        for record in self.data["payments"]:
            if str(record["payment_id"]) == str(payment_id):
                target = record
                break
        if target is None:
            return False, "That payment record could not be found."
        if status == "Verified":
            reservation = self.get_reservation(target["reservation_id"])
            total = float(reservation["total"]) if reservation else 0.0
            other_paid = sum(float(p["amount_submitted"]) for p in self.data["payments"]
                             if p["reservation_id"] == target["reservation_id"]
                             and str(p["payment_id"]) != str(payment_id)
                             and p["verification_status"] == "Verified")
            if other_paid + float(target["amount_submitted"]) > total + 0.005:
                return False, "Verifying this payment would exceed the total reservation amount."
        target["verification_status"] = status
        target["verified_at"] = business_now().isoformat(timespec="seconds")
        target["verified_by"] = admin
        if status != "Verified":
            target["approval_status"] = None
            target["approved_at"] = None
            target["approved_by"] = None
        self.save()
        reservation = self.get_reservation(target["reservation_id"])
        if reservation:
            if status == "Verified":
                self.add_notification(
                    reservation.get("username"), "Payment Verified",
                    (f"Your payment of {money(target.get('amount_submitted') or 0)} for "
                     f"reservation {target['reservation_id']} has been verified."),
                    related_type="payment", related_id=payment_id,
                    event_key=f"payment_status:{payment_id}:Verified",
                )
            elif status == "Rejected":
                self.add_notification(
                    reservation.get("username"), "Payment Rejected",
                    (f"Your payment of {money(target.get('amount_submitted') or 0)} for "
                     f"reservation {target['reservation_id']} was rejected. Please submit "
                     f"a valid payment reference."),
                    related_type="payment", related_id=payment_id,
                    event_key=f"payment_status:{payment_id}:Rejected",
                )
        if status == "Verified":
            if reservation and reservation.get("status") == "Rejected":
                self.ensure_refund_request(target["reservation_id"])
        return True, f"Payment {payment_id} status set to {status}."

    def approve_payment(self, payment_id, admin):
        """Approve a verified payment; staff and administrators may approve."""
        account = self.operational_account(admin)
        if not account:
            return False, "Only authorized staff or administrators can approve a payment."
        target = None
        for record in self.data["payments"]:
            if str(record["payment_id"]) == str(payment_id):
                target = record
                break
        if target is None:
            return False, "That payment record could not be found."
        if target.get("verification_status") != "Verified":
            return False, "Only a verified payment can be approved. Verify the payment first."
        if target.get("approval_status") == "Approved":
            return False, f"Payment {payment_id} is already approved."
        target["approval_status"] = "Approved"
        target["approved_at"] = business_now().isoformat(timespec="seconds")
        target["approved_by"] = admin
        self.save()
        reservation = self.get_reservation(target["reservation_id"])
        if reservation:
            self.add_notification(
                reservation.get("username"), "Payment Approved",
                (f"Your payment of {money(target.get('amount_submitted') or 0)} for "
                 f"reservation {target['reservation_id']} has been approved."),
                related_type="payment", related_id=payment_id,
                event_key=f"payment_approved:{payment_id}",
            )
        return True, f"Payment {payment_id} approved."

    def payment_summary(self):
        """Summarize collected, awaiting verification, and outstanding amounts."""
        by_method = {method: {"count": 0, "verified": 0.0, "pending": 0.0} for method in PAYMENT_METHODS}
        collected = 0.0
        pending_amount = 0.0
        verified_count = 0
        pending_count = 0
        for record in self.data["payments"]:
            amount = float(record.get("amount_submitted") or 0)
            method = str(record.get("method") or "")
            entry = by_method.setdefault(method, {"count": 0, "verified": 0.0, "pending": 0.0})
            entry["count"] += 1
            if record.get("verification_status") == "Verified":
                collected += amount
                verified_count += 1
                entry["verified"] += amount
            elif record.get("verification_status") == "Unverified":
                pending_amount += amount
                pending_count += 1
                entry["pending"] += amount
        billed = sum(float(r["total"]) for r in self.data["reservations"]
                     if r["status"] not in ("Cancelled", "Rejected"))
        return {
            "total_collected": round(collected, 2),
            "pending_amount": round(pending_amount, 2),
            "verified_count": verified_count,
            "pending_count": pending_count,
            "outstanding": round(max(0.0, billed - collected), 2),
            "by_method": by_method,
        }

    def sales_summary(self):
        approved_value = sum(float(r["total"]) for r in self.data["reservations"]
                              if r["status"] in ("Approved", "Completed"))
        completed_value = sum(float(r["total"]) for r in self.data["reservations"]
                              if r["status"] == "Completed")
        paid = sum(float(p["amount_submitted"]) for p in self.data["payments"]
                   if p["verification_status"] == "Verified")
        return approved_value, completed_value, paid

    def counts(self):
        data = {}
        for record in self.data["reservations"]:
            data[record["status"]] = data.get(record["status"], 0) + 1
        return {
            "total": sum(data.values()), "pending": data.get("Pending", 0),
            "approved": data.get("Approved", 0), "rejected": data.get("Rejected", 0),
            "completed": data.get("Completed", 0),
            "cancelled": data.get("Cancelled", 0),
        }

    def all_payments(self, verification="All", query=""):
        """Return payment submissions enriched with their reservation and customer."""
        needle = str(query or "").strip().lower()
        rows = []
        for payment in self.data["payments"]:
            reservation = self.get_reservation(payment.get("reservation_id")) or {}
            status = str(payment.get("verification_status") or "Unverified")
            if verification != "All" and status != verification:
                continue
            row = dict(payment)
            row["reservation"] = reservation
            row["customer"] = reservation.get("customer") or ""
            row["booking_date"] = reservation.get("booking_date") or ""
            row["service"] = reservation.get("service") or ""
            row["booking_status"] = reservation.get("status") or ""
            searchable = " ".join(str(value or "") for value in (
                payment.get("payment_id"), payment.get("reservation_id"),
                payment.get("reference"), payment.get("method"), row["customer"],
                status, payment.get("approval_status"),
            )).lower()
            if needle and needle not in searchable:
                continue
            rows.append(row)
        rows.sort(key=lambda row: (str(row.get("submitted_at") or ""),
                                   int(row.get("payment_id") or 0)), reverse=True)
        return rows

    def all_refunds(self, status="All", query=""):
        needle = str(query or "").strip().lower()
        rows = []
        for refund in self.data["refunds"]:
            current = str(refund.get("status") or "Pending")
            if status != "All" and current != status:
                continue
            reservation = self.get_reservation(refund.get("reservation_id")) or {}
            row = dict(refund)
            row["customer"] = reservation.get("customer") or ""
            row["booking_date"] = reservation.get("booking_date") or ""
            searchable = " ".join(str(value or "") for value in (
                refund.get("refund_id"), refund.get("reservation_id"),
                refund.get("username"), row["customer"], current,
                refund.get("refund_method"), refund.get("reference"),
            )).lower()
            if needle and needle not in searchable:
                continue
            rows.append(row)
        rows.sort(key=lambda row: (str(row.get("created_at") or ""),
                                   str(row.get("refund_id") or "")), reverse=True)
        return rows

    def report_statistics(self):
        rows = [dict(record) for record in self.data["reservations"]]
        service_totals = {}
        monthly = {}
        for row in rows:
            service = str(row.get("service") or "Unassigned")
            bucket = service_totals.setdefault(service, {"count": 0, "value": 0.0})
            bucket["count"] += 1
            if row.get("status") not in ("Cancelled", "Rejected"):
                bucket["value"] += float(row.get("total") or 0)
            try:
                booked = datetime.strptime(str(row.get("booking_date") or ""),
                                           "%m/%d/%Y").date()
                month_key = booked.strftime("%Y-%m")
            except ValueError:
                month_key = "Unknown"
            month = monthly.setdefault(month_key, {"count": 0, "value": 0.0})
            month["count"] += 1
            if row.get("status") not in ("Cancelled", "Rejected"):
                month["value"] += float(row.get("total") or 0)
        for bucket in service_totals.values():
            bucket["value"] = round(bucket["value"], 2)
        for bucket in monthly.values():
            bucket["value"] = round(bucket["value"], 2)
        collection = self.payment_summary()
        counts = self.counts()
        refunded = round(sum(float(refund.get("amount") or 0)
                             for refund in self.data["refunds"]
                             if refund.get("status") == "Completed"), 2)
        return {
            "counts": counts,
            "collection": collection,
            "services": dict(sorted(service_totals.items(),
                                    key=lambda item: (-item[1]["count"], item[0]))),
            "months": dict(sorted(monthly.items())),
            "refunded": refunded,
            "net_collected": round(collection["total_collected"] - refunded, 2),
        }

    def export_report_csv(self, path):
        stats = self.report_statistics()
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["WashApp Business Report"])
            writer.writerow(["Generated", business_now().isoformat(timespec="seconds")])
            writer.writerow([])
            writer.writerow(["Booking Status", "Count"])
            for key in ("total", "pending", "approved", "rejected",
                        "completed", "cancelled"):
                writer.writerow([key.title(), stats["counts"].get(key, 0)])
            writer.writerow([])
            writer.writerow(["Financial Summary", "Amount"])
            writer.writerow(["Total Collected",
                             f"{stats['collection']['total_collected']:.2f}"])
            writer.writerow(["Refunds Completed", f"{stats['refunded']:.2f}"])
            writer.writerow(["Net Collected", f"{stats['net_collected']:.2f}"])
            writer.writerow(["Outstanding", f"{stats['collection']['outstanding']:.2f}"])
            writer.writerow([])
            writer.writerow(["Service", "Bookings", "Booking Value"])
            for service, bucket in stats["services"].items():
                writer.writerow([service, bucket["count"], f"{bucket['value']:.2f}"])
            writer.writerow([])
            writer.writerow(["Month", "Bookings", "Booking Value"])
            for month, bucket in stats["months"].items():
                writer.writerow([month, bucket["count"], f"{bucket['value']:.2f}"])
        return path

    def _newest_first(self, rows):
        """Order records newest first (same ordering the old query used)."""
        return sorted(rows, key=lambda r: (str(r.get("created_at") or ""),
                                           str(r.get("reservation_id") or "")), reverse=True)

    def _verified_total(self, reservation_id):
        return sum(float(p["amount_submitted"]) for p in self.data["payments"]
                   if p["reservation_id"] == reservation_id and p["verification_status"] == "Verified")

    def _pending_total(self, reservation_id):
        return sum(float(p["amount_submitted"]) for p in self.data["payments"]
                   if p["reservation_id"] == reservation_id and p["verification_status"] == "Unverified")

    def close(self):
        self.save()


class CalendarDialog(tk.Toplevel):
    """Small dependency-free calendar picker used by history date filters."""

    def __init__(self, parent, initial=None, title="Select a date"):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=COLORS["card"])
        self.resizable(False, False)
        self.result = None
        today = business_now().date()
        initial = initial or today
        self.year = initial.year
        self.month = initial.month
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self.head = tk.Frame(self, bg=COLORS["card2"])
        self.head.pack(fill="x")
        tk.Button(self.head, text="‹", command=self.previous_month,
                  font=("Segoe UI", 14, "bold"), fg="white", bg=COLORS["button"],
                  activebackground=COLORS["cyan"], activeforeground="white",
                  relief="flat", bd=0, width=3, cursor="hand2").pack(
                      side="left", padx=8, pady=8)
        self.month_label = tk.Label(self.head, text="", font=("Segoe UI", 11, "bold"),
                                    fg="white", bg=COLORS["card2"])
        self.month_label.pack(side="left", expand=True)
        tk.Button(self.head, text="›", command=self.next_month,
                  font=("Segoe UI", 14, "bold"), fg="white", bg=COLORS["button"],
                  activebackground=COLORS["cyan"], activeforeground="white",
                  relief="flat", bd=0, width=3, cursor="hand2").pack(
                      side="right", padx=8, pady=8)

        self.grid_frame = tk.Frame(self, bg=COLORS["card"])
        self.grid_frame.pack(padx=10, pady=(8, 2))
        self.footer = tk.Frame(self, bg=COLORS["card"])
        self.footer.pack(fill="x", padx=10, pady=(4, 10))
        tk.Button(self.footer, text="TODAY", command=self.choose_today,
                  font=("Segoe UI", 8, "bold"), fg="white", bg=COLORS["button2"],
                  activebackground=COLORS["cyan"], activeforeground="white",
                  relief="flat", bd=0, padx=12, pady=7, cursor="hand2").pack(side="left")
        tk.Button(self.footer, text="CANCEL", command=self.destroy,
                  font=("Segoe UI", 8, "bold"), fg=COLORS["muted"],
                  bg=COLORS["card2"], activebackground="#1e5d78",
                  activeforeground="white", relief="flat", bd=0, padx=12, pady=7,
                  cursor="hand2").pack(side="right")
        self.render_month()
        self.update_idletasks()
        self.center_on_parent(parent)

    def center_on_parent(self, parent):
        width, height = self.winfo_width(), self.winfo_height()
        x = parent.winfo_rootx() + max(20, (parent.winfo_width() - width) // 2)
        y = parent.winfo_rooty() + max(20, (parent.winfo_height() - height) // 2)
        self.geometry(f"+{x}+{y}")

    def render_month(self):
        for child in self.grid_frame.winfo_children():
            child.destroy()
        self.month_label.configure(text=f"{calendar.month_name[self.month]} {self.year}")
        for column, name in enumerate(("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT")):
            tk.Label(self.grid_frame, text=name, font=("Segoe UI", 7, "bold"),
                     fg=COLORS["muted"], bg=COLORS["card"], width=4).grid(
                         row=0, column=column, padx=1, pady=2)
        today = business_now().date()
        for row_index, week in enumerate(calendar.monthcalendar(self.year, self.month), start=1):
            for column, day in enumerate(week):
                if day == 0:
                    tk.Label(self.grid_frame, text="", bg=COLORS["card"], width=4).grid(
                        row=row_index, column=column, padx=1, pady=1)
                    continue
                is_today = (today.year == self.year and today.month == self.month
                            and today.day == day)
                tk.Button(
                    self.grid_frame, text=str(day), width=4, pady=4,
                    font=("Segoe UI", 9, "bold" if is_today else "normal"),
                    fg=COLORS["bg"] if is_today else "white",
                    bg=COLORS["cyan"] if is_today else COLORS["card2"],
                    activebackground=COLORS["button"], activeforeground="white",
                    relief="flat", bd=0, cursor="hand2",
                    command=lambda d=day: self.choose_day(d),
                ).grid(row=row_index, column=column, padx=1, pady=1)

    def previous_month(self):
        self.month -= 1
        if self.month < 1:
            self.month = 12
            self.year -= 1
        self.render_month()

    def next_month(self):
        self.month += 1
        if self.month > 12:
            self.month = 1
            self.year += 1
        self.render_month()

    def choose_today(self):
        self.result = business_now().date()
        self.destroy()

    def choose_day(self, day):
        self.result = date(self.year, self.month, day)
        self.destroy()

    @classmethod
    def ask(cls, parent, initial=None, title="Select a date"):
        dialog = cls(parent, initial=initial, title=title)
        parent.wait_window(dialog)
        return dialog.result


class WashApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("WASHAPP - Smart Carwash Reservation System")
        self.geometry("1000x650")
        self.minsize(700, 480)
        self.configure(bg=COLORS["bg"])

        self._scroll_container = tk.Frame(self, bg=COLORS["bg"])
        self._scroll_container.pack(fill="both", expand=True)
        self._page_canvas = tk.Canvas(
            self._scroll_container, bg=COLORS["bg"], highlightthickness=0, borderwidth=0
        )
        self._page_scrollbar = ttk.Scrollbar(
            self._scroll_container, orient="vertical", command=self._page_canvas.yview
        )
        self._page_canvas.configure(yscrollcommand=self._page_scrollbar.set)
        self._page_scrollbar.pack(side="right", fill="y")
        self._page_canvas.pack(side="left", fill="both", expand=True)
        self.page = tk.Frame(self._page_canvas, bg=COLORS["bg"])
        self._page_window = self._page_canvas.create_window(
            (0, 0), window=self.page, anchor="nw"
        )
        self.page.bind("<Configure>", self._update_scroll_region)
        self._page_canvas.bind("<Configure>", self._resize_scroll_page)
        self._page_canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self._page_canvas.bind_all("<Button-4>", self._on_mousewheel_linux)
        self._page_canvas.bind_all("<Button-5>", self._on_mousewheel_linux)

        self.store = Store()
        self.current_user = None
        self.selected_vehicle = "Sedan"
        self.selected_reservation_id = None
        self.setup_styles()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.show_landing()

    def _update_scroll_region(self, _event=None):
        self._page_canvas.configure(scrollregion=self._page_canvas.bbox("all"))

    def _resize_scroll_page(self, event):

        self._page_canvas.itemconfigure(self._page_window, width=event.width)

    def _on_mousewheel(self, event):
        if getattr(event, "delta", 0):
            self._page_canvas.yview_scroll(int(-event.delta / 120), "units")

    def _on_mousewheel_linux(self, event):
        if event.num == 4:
            self._page_canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            self._page_canvas.yview_scroll(1, "units")

    def setup_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TCombobox", padding=7, fieldbackground="white", background="white", foreground="black")
        style.configure("Treeview", background=COLORS["card"], foreground="white", fieldbackground=COLORS["card"], rowheight=28)
        style.configure("Treeview.Heading", background="#173247", foreground="white", font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", COLORS["button"])], foreground=[("selected", "white")])

    def clear(self):
        job = getattr(self, "_admin_refresh_job", None)
        if job:
            try:
                self.after_cancel(job)
            except (tk.TclError, ValueError):
                pass
            self._admin_refresh_job = None
        self._admin_view_active = False
        for widget in self.page.winfo_children():
            widget.destroy()
        self.page.update_idletasks()
        self._page_canvas.yview_moveto(0)

    def header(self, title, subtitle=""):
        frame = tk.Frame(self.page, bg=COLORS["bg"])
        frame.pack(fill="x", padx=22, pady=(10, 5))
        brand_name = (self.store.setting("system_name")
                      if hasattr(self, "store") else "WashApp")
        tk.Label(frame, text=brand_name.upper(), font=("Segoe UI", 22, "bold"),
                 fg=COLORS["cyan"], bg=COLORS["bg"]).pack(anchor="w")
        tk.Label(frame, text=title, font=("Segoe UI", 15, "bold"), fg="white", bg=COLORS["bg"]).pack(anchor="w")
        if subtitle:
            tk.Label(frame, text=subtitle, font=("Segoe UI", 9), fg="#d9f7ff", bg=COLORS["bg"]).pack(anchor="w")

    def card(self, parent, **kwargs):
        return tk.Frame(parent, bg=COLORS["card"], highlightbackground="#23374e", highlightthickness=1, **kwargs)

    def btn(self, parent, label, command, primary=True, **kwargs):
        return tk.Button(parent, text=label, command=command, font=("Segoe UI", 9, "bold"),
                         fg="white", bg=COLORS["button"] if primary else COLORS["button2"],
                         activebackground=COLORS["cyan"], activeforeground="white",
                         relief="flat", bd=0, cursor="hand2", padx=12, pady=9, **kwargs)

    def label(self, parent, text, size=10, color="white", bold=False, **kwargs):
        return tk.Label(parent, text=text, font=("Segoe UI", size, "bold" if bold else "normal"),
                        fg=color, bg=kwargs.pop("bg", COLORS["card"]), **kwargs)

    def is_admin_user(self):
        return bool(self.current_user and self.current_user.get("role") == "admin")

    def is_staff_or_admin(self):
        return bool(self.current_user and
                    self.current_user.get("role") in ("admin", "staff"))

    def field_label(self, parent, text, row, column, padx=(0, 6), pady=(0, 3)):
        self.label(parent, text, 8, COLORS["muted"], True).grid(
            row=row, column=column, sticky="w", padx=padx, pady=pady)

    def form_entry(self, parent, variable, width=24, show=None):
        entry = tk.Entry(
            parent, textvariable=variable, width=width, show=show or "",
            font=("Segoe UI", 10), relief="flat", bg=COLORS["card2"], fg="white",
            insertbackground="white", highlightbackground="#2c4764",
            highlightthickness=1,
        )
        return entry

    def date_filter_field(self, parent, label_text, initial=""):
        variable = tk.StringVar(value=initial)
        field = tk.Frame(parent, bg=COLORS["card"])
        self.label(field, label_text, 8, COLORS["muted"], True).pack(anchor="w")
        row = tk.Frame(field, bg=COLORS["card"])
        row.pack(anchor="w", pady=(3, 0))
        entry = self.form_entry(row, variable, width=13)
        entry.pack(side="left", ipady=6)
        self.btn(row, "CALENDAR", lambda: self.pick_date_for_entry(entry),
                 primary=False).pack(side="left", padx=(5, 0))
        return variable, field

    def pick_date_for_entry(self, entry):
        current = None
        try:
            current = datetime.strptime(entry.get().strip(), "%m/%d/%Y").date()
        except ValueError:
            current = business_now().date()
        picked = CalendarDialog.ask(self, initial=current, title="Choose a date")
        if picked:
            entry.delete(0, "end")
            entry.insert(0, picked.strftime("%m/%d/%Y"))

    def parse_date_text(self, text, field_name, required=False):
        text = str(text or "").strip()
        if not text:
            if required:
                raise ValueError(f"Select a {field_name.lower()}.")
            return None
        try:
            return datetime.strptime(text, "%m/%d/%Y").date()
        except ValueError:
            raise ValueError(f"Enter the {field_name.lower()} as MM/DD/YYYY.")

    def section_heading(self, parent, title, note=""):
        row = tk.Frame(parent, bg=COLORS["bg"])
        row.pack(fill="x", pady=(0, 6))
        self.label(row, title, 12, COLORS["cyan"], True, bg=COLORS["bg"]).pack(side="left")
        if note:
            self.label(row, note, 8, COLORS["muted"], bg=COLORS["bg"]).pack(side="right")
        return row

    def empty_state(self, parent, title, message):
        card = self.card(parent)
        card.pack(fill="x")
        self.label(card, title, 12, COLORS["cyan"], True).pack(
            anchor="w", padx=20, pady=(18, 4))
        self.label(card, message, 9, COLORS["muted"], wraplength=720,
                   justify="left").pack(anchor="w", padx=20, pady=(0, 18))

    def configure_customer_table_style(self):
        style = ttk.Style(self)
        style.configure(
            "Customer.Treeview",
            background=COLORS["card2"],
            fieldbackground=COLORS["card2"],
            foreground=COLORS["white"],
            rowheight=34,
            font=("Segoe UI", 9),
            borderwidth=0,
        )
        style.configure(
            "Customer.Treeview.Heading",
            background="#14273b",
            foreground=COLORS["cyan"],
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            padding=(8, 8),
        )
        style.map(
            "Customer.Treeview",
            background=[("selected", COLORS["button"])],
            foreground=[("selected", "white")],
        )
        style.map("Customer.Treeview.Heading", background=[("active", "#1a3450")])

    def customer_nav_button(self, parent, label, command, active=False):
        return tk.Button(
            parent,
            text=label,
            command=command,
            font=("Segoe UI", 9, "bold"),
            fg="white" if active else COLORS["muted"],
            bg=COLORS["button"] if active else COLORS["card2"],
            activebackground="#1e5d78",
            activeforeground="white",
            relief="flat",
            bd=0,
            cursor="hand2",
            anchor="w",
            padx=16,
            pady=10,
        )

    def customer_shell(self, active, title, subtitle=""):
        self.clear()
        full_name = str(self.current_user.get("full_name") or "Customer") if self.current_user else "Customer"
        initials = "".join(part[0] for part in full_name.split() if part)[:2].upper() or "CU"
        settings = self.store.settings()
        self.title(f"{settings['system_name']} - Customer Portal")

        header = tk.Frame(self.page, bg=COLORS["card2"], highlightbackground="#1d344b",
                          highlightthickness=1)
        header.pack(fill="x", padx=18, pady=(12, 10))
        brand = tk.Frame(header, bg=COLORS["card2"])
        brand.pack(side="left", padx=20, pady=10)
        self.label(brand, settings["system_name"].upper(), 20, COLORS["cyan"], True,
                   bg=COLORS["card2"]).pack(anchor="w")
        self.label(brand, settings["system_subtitle"].upper(), 7, COLORS["muted"], True,
                   bg=COLORS["card2"]).pack(anchor="w")

        account = tk.Frame(header, bg=COLORS["card2"])
        account.pack(side="right", padx=16, pady=8)
        self.btn(account, "LOGOUT", self.logout, primary=False, width=10).pack(
            side="right", padx=(12, 0))
        profile = tk.Frame(account, bg=COLORS["card2"])
        profile.pack(side="right")
        avatar = tk.Label(profile, text=initials, font=("Segoe UI", 10, "bold"),
                          fg=COLORS["bg"], bg=COLORS["cyan"], width=3, pady=6)
        avatar.pack(side="left", padx=(0, 8))
        identity = tk.Frame(profile, bg=COLORS["card2"])
        identity.pack(side="left")
        self.label(identity, "WELCOME,", 7, COLORS["muted"], True,
                   bg=COLORS["card2"]).pack(anchor="w")
        self.label(identity, full_name, 10, COLORS["white"], True,
                   bg=COLORS["card2"]).pack(anchor="w")
        self.label(identity, "CUSTOMER ACCOUNT", 7, COLORS["cyan"], True,
                   bg=COLORS["card2"]).pack(anchor="w")

        body = tk.Frame(self.page, bg=COLORS["bg"])
        body.pack(fill="both", expand=True, padx=18, pady=(0, 16))
        sidebar = tk.Frame(body, bg=COLORS["card2"], width=205,
                           highlightbackground="#1d344b", highlightthickness=1)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        self.label(sidebar, "CUSTOMER PORTAL", 8, COLORS["muted"], True,
                   bg=COLORS["card2"]).pack(anchor="w", padx=16, pady=(18, 10))
        unread_count = self.store.unread_notification_count(
            self.current_user["username"]) if self.current_user else 0
        notification_label = ("NOTIFICATIONS" if not unread_count
                              else f"NOTIFICATIONS ({unread_count})")
        nav_items = [
            ("dashboard", "DASHBOARD", self.show_customer_dashboard),
            ("history", "BOOKING HISTORY", self.show_customer_booking_history),
            ("account", "MY ACCOUNT", self.show_account_settings),
            ("notifications", notification_label, self.show_customer_notifications),
        ]
        for key, nav_label, command in nav_items:
            self.customer_nav_button(sidebar, nav_label, command, key == active).pack(
                fill="x", padx=10, pady=2)
        support = tk.Frame(sidebar, bg=COLORS["card2"])
        support.pack(side="bottom", fill="x", padx=10, pady=12)
        self.label(support, "NEED HELP?", 8, COLORS["cyan"], True,
                   bg=COLORS["card2"]).pack(anchor="w", padx=8)
        self.label(support, "Contact 09123456789.",
                   8, COLORS["muted"], bg=COLORS["card2"], wraplength=160,
                   justify="left").pack(anchor="w", padx=8, pady=(2, 6))

        content = tk.Frame(body, bg=COLORS["bg"])
        content.pack(side="left", fill="both", expand=True, padx=(14, 0))
        self.label(content, title, 18, COLORS["white"], True,
                   bg=COLORS["bg"]).pack(anchor="w")
        if subtitle:
            self.label(content, subtitle, 9, COLORS["muted"], bg=COLORS["bg"]).pack(
                anchor="w", pady=(2, 12))
        return content

    def configure_admin_table_style(self):
        style = ttk.Style(self)
        style.configure(
            "Admin.Treeview",
            background=COLORS["card2"],
            fieldbackground=COLORS["card2"],
            foreground=COLORS["white"],
            rowheight=32,
            font=("Segoe UI", 9),
            borderwidth=0,
        )
        style.configure(
            "Admin.Treeview.Heading",
            background="#14273b",
            foreground="white",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            padding=(7, 8),
        )
        style.map(
            "Admin.Treeview",
            background=[("selected", COLORS["button"])],
            foreground=[("selected", "white")],
        )
        style.map("Admin.Treeview.Heading", background=[("active", "#1a3450")])

    def admin_nav_button(self, parent, label, command, active=False):
        return tk.Button(
            parent, text=label, command=command, font=("Segoe UI", 8, "bold"),
            fg="white" if active else COLORS["muted"],
            bg=COLORS["button"] if active else COLORS["card2"],
            activebackground="#1e5d78", activeforeground="white",
            relief="flat", bd=0, cursor="hand2", anchor="w",
            padx=12, pady=7,
        )

    def admin_shell(self, title, subtitle="", active=""):
        self.clear()
        full_name = str(self.current_user.get("full_name") or "Administrator") if self.current_user else "Administrator"
        initials = "".join(part[0] for part in full_name.split() if part)[:2].upper() or "AD"
        settings = self.store.settings()
        self.title(f"{settings['system_name']} - "
                   f"{'Admin' if self.is_admin_user() else 'Staff'} Console")

        header = tk.Frame(self.page, bg=COLORS["card2"], highlightbackground="#1d344b",
                          highlightthickness=1)
        header.pack(fill="x", padx=18, pady=(12, 10))
        brand = tk.Frame(header, bg=COLORS["card2"])
        brand.pack(side="left", padx=20, pady=10)
        self.label(brand, settings["system_name"].upper(), 20, COLORS["cyan"], True,
                   bg=COLORS["card2"]).pack(anchor="w")
        self.label(brand, settings["system_subtitle"].upper(), 7, COLORS["muted"], True,
                   bg=COLORS["card2"]).pack(anchor="w")

        account = tk.Frame(header, bg=COLORS["card2"])
        account.pack(side="right", padx=16, pady=8)
        self.btn(account, "LOGOUT", self.logout, primary=False, width=10).pack(
            side="right", padx=(12, 0))
        profile = tk.Frame(account, bg=COLORS["card2"])
        profile.pack(side="right")
        avatar = tk.Label(profile, text=initials, font=("Segoe UI", 10, "bold"),
                          fg=COLORS["bg"], bg=COLORS["cyan"], width=3, pady=6)
        avatar.pack(side="left", padx=(0, 8))
        identity = tk.Frame(profile, bg=COLORS["card2"])
        identity.pack(side="left")
        self.label(identity, "ADMIN CONSOLE" if self.is_admin_user() else "STAFF CONSOLE",
                   7, COLORS["muted"], True, bg=COLORS["card2"]).pack(anchor="w")
        self.label(identity, full_name, 10, COLORS["white"], True,
                   bg=COLORS["card2"]).pack(anchor="w")
        self.label(identity, "ADMINISTRATOR" if self.is_admin_user() else "CARWASH STAFF",
                   7, COLORS["cyan"], True, bg=COLORS["card2"]).pack(anchor="w")

        self.store.sync_admin_notifications()
        body = tk.Frame(self.page, bg=COLORS["bg"])
        body.pack(fill="both", expand=True, padx=18, pady=(0, 16))
        sidebar = tk.Frame(body, bg=COLORS["card2"], width=190,
                           highlightbackground="#1d344b", highlightthickness=1)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        self.label(sidebar, "ADMIN NAVIGATION" if self.is_admin_user() else "STAFF NAVIGATION",
                   8, COLORS["muted"], True,
                   bg=COLORS["card2"]).pack(anchor="w", padx=12, pady=(14, 8))
        unread_count = self.store.unread_notification_count(
            self.current_user["username"]) if self.current_user else 0
        notification_label = ("NOTIFICATIONS" if not unread_count
                              else f"NOTIFICATIONS ({unread_count})")
        nav_items = [
            ("dashboard", "DASHBOARD", self.show_admin_dashboard, ("admin", "staff")),
            ("manage", "MANAGE BOOKINGS", self.show_admin_manage_bookings,
             ("admin", "staff")),
            ("history", "BOOKING HISTORY", self.show_admin_booking_history,
             ("admin", "staff")),
            ("verification", "PAYMENT VERIFICATION",
             self.show_admin_payment_verification, ("admin", "staff")),
            ("refunds", "REFUNDS", self.show_admin_refunds, ("admin",)),
            ("notifications", notification_label, self.show_admin_notifications,
             ("admin", "staff")),
            ("reports", "REPORTS", self.show_admin_reports, ("admin",)),
            ("settings", "SYSTEM SETTINGS", self.show_admin_settings, ("admin",)),
            ("accounts", "ACCOUNTS", self.show_admin_accounts, ("admin",)),
            ("services", "SERVICES & PRICING", self.show_admin_services, ("admin",)),
        ]
        role = self.current_user.get("role") if self.current_user else ""
        for key, nav_label, command, allowed_roles in nav_items:
            if role not in allowed_roles:
                continue
            self.admin_nav_button(sidebar, nav_label, command, key == active).pack(
                fill="x", padx=8, pady=1)
        self.label(sidebar,
                   f"Signed in as {'administrator' if self.is_admin_user() else 'staff'}",
                   7, COLORS["muted"], bg=COLORS["card2"], wraplength=155,
                   justify="left").pack(side="bottom", anchor="w", padx=12, pady=12)

        content = tk.Frame(body, bg=COLORS["bg"])
        content.pack(side="left", fill="both", expand=True, padx=(14, 0))
        self.label(content, title, 18, COLORS["white"], True,
                   bg=COLORS["bg"]).pack(anchor="w")
        if subtitle:
            self.label(content, subtitle, 9, COLORS["muted"], bg=COLORS["bg"]).pack(
                anchor="w", pady=(2, 12))
        return content

    def admin_metric_card(self, parent, label, value, icon, accent):
        cell = tk.Frame(parent, bg=COLORS["card"], height=84,
                        highlightbackground="#23374e", highlightthickness=1)
        cell.grid_propagate(False)
        tk.Frame(cell, bg=accent, width=4).pack(side="left", fill="y")
        body = tk.Frame(cell, bg=COLORS["card"])
        body.pack(side="left", fill="both", expand=True, padx=12, pady=9)
        top = tk.Frame(body, bg=COLORS["card"])
        top.pack(fill="x")
        tk.Label(top, text=icon, font=("Segoe UI Symbol", 11, "bold"), fg=accent,
                 bg=COLORS["card2"], width=2, pady=2).pack(side="left")
        tk.Label(top, text=label, font=("Segoe UI", 8, "bold"), fg=COLORS["muted"],
                 bg=COLORS["card"], anchor="w", justify="left", wraplength=145).pack(
                     side="left", padx=(6, 0))
        tk.Label(body, text=str(value), font=("Segoe UI", 16, "bold"),
                 fg=COLORS["white"], bg=COLORS["card"], anchor="w").pack(
                     anchor="w", pady=(5, 0))
        return cell

    def admin_payment_metric(self, parent, label, value, color):
        cell = tk.Frame(parent, bg=COLORS["card2"], highlightbackground="#23374e",
                        highlightthickness=1)
        self.label(cell, label, 8, COLORS["muted"], True, bg=COLORS["card2"]).pack(
            anchor="w", padx=12, pady=(9, 2))
        self.label(cell, str(value), 13, color, True, bg=COLORS["card2"]).pack(
            anchor="w", padx=12, pady=(0, 9))
        return cell

    def update_admin_action_state(self):
        selected = bool(self.admin_tree.selection()) if hasattr(self, "admin_tree") else False
        for button in getattr(self, "_admin_action_buttons", []):
            try:
                button.configure(state="normal" if selected else "disabled",
                                 cursor="hand2" if selected else "arrow")
            except tk.TclError:
                pass
        hint = getattr(self, "admin_action_hint", None)
        if hint is not None:
            try:
                hint.configure(
                    text=("Reservation selected. Choose an action."
                          if selected else "Select a reservation to enable actions."),
                    fg=COLORS["cyan"] if selected else COLORS["muted"],
                )
            except tk.TclError:
                pass

    def show_landing(self):
        self.current_user = None
        self.clear()
        settings = self.store.settings()
        self.title(f"{settings['system_name']} - {settings['system_subtitle']}")
        self.header(settings["system_subtitle"], settings["tagline"])
        body = tk.Frame(self.page, bg=COLORS["bg"])
        body.pack(fill="both", expand=True, padx=22, pady=(0, 8))

        self.label(body, "SELECT YOUR VEHICLE", 19, COLORS["white"], True, bg=COLORS["bg"]).pack(pady=(8, 3))
        self.label(body, "Choose your vehicle type to continue.", 10, COLORS["cyan"], True, bg=COLORS["bg"]).pack(pady=(0, 12))

        vehicle_row = tk.Frame(body, bg=COLORS["bg"])
        vehicle_row.pack()
        vehicle_icons = {"Sedan": "🚘", "SUV": "🚙", "Van": "🚐", "Pick-up": "🛻", "Motorcycle": "🏍"}
        for vehicle in VEHICLES:
            card = self.card(vehicle_row, width=125, height=150)
            card.pack(side="left", padx=5)
            card.pack_propagate(False)
            self.label(card, vehicle_icons.get(vehicle, "🚗"), 28, COLORS["cyan"]).pack(pady=(10, 5))
            self.label(card, vehicle, 11, COLORS["white"], True).pack(pady=(0, 6))
            self.btn(card, "SELECT", lambda v=vehicle: self.select_landing_vehicle(v), width=9).pack()

        self.landing_vehicle = tk.StringVar(value="")
        self.landing_service = tk.StringVar(value="")
        selector = self.card(body)
        selector.pack(fill="x", padx=45, pady=(14, 5))
        self.label(selector, "SERVICE AND PRICE", 13, COLORS["cyan"], True).pack(pady=(9, 6))
        controls = tk.Frame(selector, bg=COLORS["card"])
        controls.pack(padx=12, pady=(0, 8))
        self.label(controls, "Vehicle", 10, bold=True).grid(row=0, column=0, padx=8, pady=5)
        self.landing_vehicle_display = ttk.Combobox(controls, textvariable=self.landing_vehicle, values=VEHICLES, state="readonly", width=15)
        self.landing_vehicle_display.grid(row=0, column=1, padx=8, pady=5)
        self.landing_vehicle_display.bind("<<ComboboxSelected>>", lambda e: self.refresh_landing_prices())
        self.label(controls, "Service", 10, bold=True).grid(row=0, column=2, padx=8, pady=5)
        self.landing_service_combo = ttk.Combobox(controls, textvariable=self.landing_service, values=[], state="disabled", width=28)
        self.landing_service_combo.grid(row=0, column=3, padx=8, pady=5)
        self.landing_service_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_landing_prices())
        self.btn(body, "CONFIRM AND CONTINUE", self.confirm_landing_selection, width=27).pack(pady=(8, 3))
        self.label(body, "After confirming, choose to log in or create a customer account.", 9, COLORS["muted"], bg=COLORS["bg"]).pack(pady=(2, 0))

        self.btn(body, "ADMIN ACCOUNT", self.show_admin_login, primary=False, width=20).pack(pady=(9, 3))

    def select_landing_vehicle(self, vehicle):
        self.landing_vehicle.set(vehicle)
        self.refresh_landing_prices()

    def refresh_landing_prices(self):
        vehicle = self.landing_vehicle.get()
        if not vehicle:
            self.landing_service_combo.configure(state="disabled", values=[])
            return
        options = service_options(vehicle)
        if not SERVICES:
            self.landing_service_combo.configure(state="disabled", values=[])
            self.landing_service.set("")
            return
        available_services = service_names_for_vehicle(vehicle)
        self.landing_service_combo.configure(state="readonly", values=options)
        current_service = service_name(self.landing_service.get()) if self.landing_service.get() else ""
        if current_service not in available_services:
            current_service = available_services[0] if available_services else ""
        if not current_service:
            self.landing_service.set("")
            self.landing_service_combo.configure(state="disabled", values=[])
            return
        selected_label = f"{current_service} — {money(PRICES[current_service][vehicle])}"
        self.landing_service.set(selected_label)

    def confirm_landing_selection(self):
        vehicle = self.landing_vehicle.get()
        service = service_name(self.landing_service.get())
        if not vehicle:
            messagebox.showwarning("Select Vehicle", "Please select a vehicle first.")
            return
        if service not in service_names_for_vehicle(vehicle):
            messagebox.showwarning("Select Service", "Please select a service available for this vehicle.")
            return
        self.selected_vehicle = vehicle
        self.landing_selected_service = service
        self.show_continue_choice()

    def show_continue_choice(self):
        self.clear()
        self.header("Ready to Continue?", "Your vehicle and service selection are ready.")
        box = self.card(self.page)
        box.pack(fill="x", padx=260, pady=35)
        self.label(box, "YOUR SELECTION", 15, COLORS["cyan"], True).pack(pady=(25, 14))
        self.label(box, f"Vehicle: {self.selected_vehicle}", 12, bold=True).pack(pady=5)
        service = getattr(self, "landing_selected_service", SERVICES[0])
        self.label(box, f"Service: {service}", 12, bold=True).pack(pady=5)
        self.label(box, f"Price: {money(PRICES[service][self.selected_vehicle])}", 14, COLORS["cyan"], True).pack(pady=8)
        self.label(box, "Log in to an existing account or register as a new customer.", 10, COLORS["muted"]).pack(pady=(4, 16))
        self.btn(box, "LOGIN", self.show_login, width=20).pack(pady=6)
        self.btn(box, "CREATE CUSTOMER ACCOUNT", self.show_register, primary=False, width=25).pack(pady=6)
        self.btn(box, "BACK TO VEHICLE SELECTION", self.show_landing, primary=False).pack(pady=(12, 24))

    def show_login(self):
        self.clear()
        self.header("Login", "Sign in to access customer reservations or administration.")
        box = self.card(self.page)
        box.pack(fill="x", padx=250, pady=25)
        self.label(box, "USERNAME", 10, COLORS["cyan"], True).pack(anchor="w", padx=30, pady=(25, 5))
        username = tk.Entry(box, font=("Segoe UI", 11), relief="flat")
        username.pack(fill="x", padx=30, ipady=8)
        self.label(box, "PASSWORD", 10, COLORS["cyan"], True).pack(anchor="w", padx=30, pady=(15, 5))
        password = tk.Entry(box, show="*", font=("Segoe UI", 11), relief="flat")
        password.pack(fill="x", padx=30, ipady=8)
        def login():
            name, pwd = username.get().strip(), password.get()
            if not name or not pwd:
                messagebox.showwarning("Missing Information", "Enter both username and password.")
                return
            user = self.store.authenticate(name, pwd)
            if not user:
                existing = self.store.user(name)
                if existing and str(existing.get("status") or "Active") != "Active":
                    messagebox.showerror("Account Inactive",
                                         "This account has been deactivated. Contact an administrator.")
                else:
                    messagebox.showerror("Login Failed", "Invalid username or password.")
                return
            self.current_user = dict(user)
            if user["role"] in ("admin", "staff"):
                self.show_admin_dashboard()
            else:
                self.show_customer_dashboard()
        self.btn(box, "LOGIN", login, width=15).pack(pady=15)
        self.btn(box, "BACK TO LANDING PAGE", self.show_landing, primary=False).pack(pady=(0, 25))

    def show_admin_login(self):
        """Display a dedicated login page that accepts administrator accounts only."""
        self.clear()
        self.header("Admin Login", "Authorized WASHAPP administrators only.")
        box = self.card(self.page)
        box.pack(fill="x", padx=250, pady=35)
        self.label(box, "ADMINISTRATOR SIGN IN", 15, COLORS["cyan"], True).pack(pady=(25, 15))
        self.label(box, "USERNAME", 10, COLORS["cyan"], True).pack(anchor="w", padx=10, pady=(5, 5))
        username = tk.Entry(box, font=("Segoe UI", 11), relief="flat")
        username.pack(fill="x", padx=30, ipady=8)
        self.label(box, "PASSWORD", 10, COLORS["cyan"], True).pack(anchor="w", padx=10, pady=(5, 5))
        password = tk.Entry(box, show="*", font=("Segoe UI", 11), relief="flat")
        password.pack(fill="x", padx=30, ipady=8)

        def login_admin():
            name, pwd = username.get().strip(), password.get()
            if not name or not pwd:
                messagebox.showwarning("Missing Information", "Enter both admin username and password.")
                return
            user = self.store.authenticate(name, pwd)
            if not user:
                messagebox.showerror("Login Failed", "Invalid or inactive admin credentials.")
                return
            if user["role"] != "admin":
                messagebox.showerror("Access Denied", "This page is for admin accounts only.")
                return
            self.current_user = dict(user)
            self.show_admin_dashboard()

        self.btn(box, "ADMIN LOGIN", login_admin, width=20).pack(pady=20)
        self.btn(box, "BACK TO LANDING PAGE", self.show_landing, primary=False).pack(pady=(0, 25))

    def show_register(self):
        self.clear()
        self.header("Customer Registration", "Create your WASHTAPP customer account.")
        box = self.card(self.page)
        box.pack(fill="x", padx=250, pady=10)
        fields = {}
        for name, secret in [("Full Name", False), ("Username", False), ("Password", True), ("Confirm Password", True), ("Contact Number", False)]:
            self.label(box, name, 10, COLORS["cyan"], True).pack(anchor="w", padx=30, pady=(10, 3))
            entry = tk.Entry(box, show="*" if secret else "", font=("Segoe UI", 11), relief="flat")
            entry.pack(fill="x", padx=30, ipady=7)
            fields[name] = entry

        def register():
            full = fields["Full Name"].get().strip()
            username = fields["Username"].get().strip()
            password = fields["Password"].get()
            confirm = fields["Confirm Password"].get()
            contact = fields["Contact Number"].get().strip()
            if not all([full, username, password, confirm, contact]):
                messagebox.showwarning("Incomplete Registration", "Please fill in every field.")
                return
            if len(username) < 3 or any(ch.isspace() for ch in username):
                messagebox.showwarning("Invalid Username", "Username must be at least 3 characters and contain no spaces.")
                return
            if len(password) < 6:
                messagebox.showwarning("Weak Password", "Use a password with at least 6 characters.")
                return
            if password != confirm:
                messagebox.showerror("Registration Failed", "Passwords do not match.")
                return
            if self.store.user(username):
                messagebox.showerror("Registration Failed", "That username already exists.")
                return
            ok, message = self.store.register(username, password, full, contact)
            if not ok:
                messagebox.showwarning("Registration Failed", message)
                return
            messagebox.showinfo("Success", "Account created. You can now log in.")
            self.show_login()
        self.btn(box, "CREATE ACCOUNT", register, width=22).pack(pady=18)
        self.btn(box, "BACK", self.show_landing, primary=False).pack(pady=(0, 20))

    def show_customer_dashboard(self):
        content = self.customer_shell(
            "dashboard",
            "Customer Dashboard",
            "Manage your carwash reservations, payments, and account updates.",
        )
        username = self.current_user["username"]
        rows = self.store.reservations_for_user(username)
        for record in rows:
            if record.get("status") == "Rejected":
                self.store.ensure_refund_request(record["reservation_id"])
        full_name = self.current_user.get("full_name") or "Customer"

        welcome = self.card(content)
        welcome.pack(fill="x", pady=(0, 12))
        accent = tk.Frame(welcome, bg=COLORS["cyan"], width=5)
        accent.pack(side="left", fill="y")
        welcome_body = tk.Frame(welcome, bg=COLORS["card"])
        welcome_body.pack(side="left", fill="both", expand=True, padx=20, pady=16)
        self.label(welcome_body, "WELCOME BACK", 8, COLORS["cyan"], True).pack(anchor="w")
        self.label(welcome_body, full_name, 19, COLORS["white"], True).pack(
            anchor="w", pady=(2, 3))
        self.label(
            welcome_body,
            "Track your bookings, settle balances, and keep your account details up to date.",
            9, COLORS["muted"], wraplength=380, justify="left"
        ).pack(anchor="w")

        action_bar = tk.Frame(content, bg=COLORS["bg"])
        action_bar.pack(fill="x", pady=(0, 12))
        self.btn(action_bar, "NEW BOOKING", self.show_booking, width=18).pack(
            side="left", padx=(0, 8))
        self.btn(action_bar, "REFRESH", self.show_customer_dashboard,
                 primary=False, width=12).pack(side="left")
        count_text = f"{len(rows)} reservation{'s' if len(rows) != 1 else ''}"
        self.label(action_bar, count_text, 9, COLORS["muted"], True,
                   bg=COLORS["bg"]).pack(side="right", padx=4)

        section = tk.Frame(content, bg=COLORS["bg"])
        section.pack(fill="x", pady=(0, 8))
        self.label(section, "MY RESERVATIONS", 12, COLORS["cyan"], True,
                   bg=COLORS["bg"]).pack(side="left")
        self.label(section, "Select a row to view its payment and receipt details.",
                   8, COLORS["muted"], bg=COLORS["bg"]).pack(side="right")

        if not rows:
            empty = self.card(content)
            empty.pack(fill="x")
            self.label(empty, "NO RESERVATIONS YET", 12, COLORS["cyan"], True).pack(
                anchor="w", padx=22, pady=(20, 4))
            self.label(empty, "Your carwash bookings will appear here once you create one.",
                       9, COLORS["muted"]).pack(anchor="w", padx=22, pady=(0, 20))
            return

        table_frame = self.card(content)
        table_frame.pack(fill="x", pady=(0, 10))
        cols = ("id", "date", "time", "vehicle", "service", "total", "paid",
                "balance", "status", "payment")
        headings = {
            "id": "Reservation ID", "date": "Date", "time": "Time",
            "vehicle": "Vehicle", "service": "Service", "total": "Total",
            "paid": "Amount Paid", "balance": "Remaining Balance",
            "status": "Booking Status", "payment": "Payment Status",
        }
        widths = {
            "id": 96, "date": 88, "time": 74, "vehicle": 80, "service": 112,
            "total": 78, "paid": 88, "balance": 96, "status": 96, "payment": 98,
        }
        self.configure_customer_table_style()
        tree = ttk.Treeview(
            table_frame, columns=cols, show="headings", style="Customer.Treeview",
            selectmode="browse", height=min(max(len(rows), 4), 9)
        )
        for column in cols:
            tree.heading(column, text=headings[column])
            tree.column(column, width=widths[column], anchor="center")
        tree.tag_configure("pending", foreground=RESERVATION_STATUS_COLORS["Pending"])
        tree.tag_configure("approved", foreground=RESERVATION_STATUS_COLORS["Approved"])
        tree.tag_configure("rejected", foreground=RESERVATION_STATUS_COLORS["Rejected"])
        tree.tag_configure("completed", foreground=RESERVATION_STATUS_COLORS["Completed"])
        tree.tag_configure("cancelled", foreground="#ff9f6b")

        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        tree.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 0))
        v_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        h_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=tree.xview)
        v_scroll.grid(row=0, column=1, sticky="ns", pady=(8, 0))
        h_scroll.grid(row=1, column=0, sticky="ew")
        tree.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)

        for record in rows:
            pay_status, confirmed, pending, remaining = self.store.payment_status(
                record["reservation_id"])
            status_key = str(record["status"]).strip().lower()
            row_tag = (status_key if status_key in
                       ("rejected", "approved", "pending", "completed", "cancelled")
                       else "")
            tree.insert(
                "", "end", iid=record["reservation_id"],
                values=(
                    record["reservation_id"], record["booking_date"],
                    record["booking_time"], record["vehicle"], record["service"],
                    money(record["total"]), money(confirmed), money(remaining),
                    record["status"], pay_status,
                ),
                tags=(row_tag,) if row_tag else (),
            )

        actions = tk.Frame(content, bg=COLORS["bg"])
        actions.pack(fill="x", pady=(0, 12))
        self.btn(actions, "VIEW SELECTED / PAYMENT / RECEIPT",
                 lambda: self.customer_reservation_action(tree), width=32).pack(side="left")
        self.label(actions, "You can also double-click a booking row.",
                   9, COLORS["muted"], bg=COLORS["bg"]).pack(side="left", padx=12)
        tree.bind("<Double-1>", lambda _event: self.customer_reservation_action(tree))

    def show_customer_booking_history(self):
        content = self.customer_shell(
            "history", "Booking History",
            "Review completed, pending, cancelled, and rejected bookings by booking date.",
        )
        filters = self.card(content)
        filters.pack(fill="x", pady=(0, 10))
        body = tk.Frame(filters, bg=COLORS["card"])
        body.pack(fill="x", padx=14, pady=12)
        self.label(body, "DATE RANGE", 8, COLORS["cyan"], True).grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 6))
        start_var, start_field = self.date_filter_field(body, "FROM (MM/DD/YYYY)")
        start_field.grid(row=1, column=0, sticky="w", padx=(0, 10))
        end_var, end_field = self.date_filter_field(body, "TO (MM/DD/YYYY)")
        end_field.grid(row=1, column=1, sticky="w")
        buttons = tk.Frame(body, bg=COLORS["card"])
        buttons.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        button_specs = [
            ("APPLY FILTER", lambda: draw_history(), True),
            ("THIS MONTH", lambda: set_preset("month"), False),
            ("LAST 30 DAYS", lambda: set_preset("30"), False),
            ("CLEAR", lambda: set_preset("clear"), False),
        ]
        for index, (text, command, primary) in enumerate(button_specs):
            self.btn(buttons, text, command, primary=primary).grid(
                row=index // 2, column=index % 2, sticky="ew",
                padx=(0, 5), pady=2)
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        self.label(body, "Dates use the booking date, not the request date.",
                   8, COLORS["muted"]).grid(row=3, column=0, columnspan=2,
                                            sticky="w", pady=(7, 0))

        results = tk.Frame(content, bg=COLORS["bg"])
        results.pack(fill="both", expand=True)

        def set_preset(kind):
            today = business_now().date()
            if kind == "month":
                start_var.set(today.replace(day=1).strftime("%m/%d/%Y"))
                end_var.set(today.strftime("%m/%d/%Y"))
            elif kind == "30":
                start_var.set((today - timedelta(days=29)).strftime("%m/%d/%Y"))
                end_var.set(today.strftime("%m/%d/%Y"))
            else:
                start_var.set("")
                end_var.set("")
            draw_history()

        def draw_history():
            for child in results.winfo_children():
                child.destroy()
            try:
                start = self.parse_date_text(start_var.get(), "start date")
                end = self.parse_date_text(end_var.get(), "end date")
            except ValueError as error:
                messagebox.showwarning("Invalid Date Range", str(error))
                return
            if start and end and start > end:
                messagebox.showwarning("Invalid Date Range",
                                       "The start date cannot be after the end date.")
                return
            rows = self.store.reservations_history(
                self.current_user["username"], start, end)
            summary = self.store.history_summary(rows, customer=True)
            summary_card = self.card(results)
            summary_card.pack(fill="x", pady=(0, 9))
            summary_grid = tk.Frame(summary_card, bg=COLORS["card"])
            summary_grid.pack(fill="x", padx=8, pady=8)
            summary_items = [
                ("Bookings", summary["count"], COLORS["cyan"]),
                ("Completed", summary["completed"], "#36d98a"),
                ("Cancelled", summary["cancelled"], "#ff9f6b"),
                ("Rejected", summary["rejected"], "#ff6b6b"),
                ("Booking Value", money(summary["value"]), COLORS["white"]),
                ("Verified Paid", money(summary["paid"]), "#8fb8ff"),
            ]
            for index, (name, value, color) in enumerate(summary_items):
                cell = tk.Frame(summary_grid, bg=COLORS["card2"],
                                highlightbackground="#23374e", highlightthickness=1)
                cell.grid(row=index // 3, column=index % 3, sticky="nsew", padx=4, pady=4)
                summary_grid.columnconfigure(index % 3, weight=1)
                self.label(cell, name, 8, COLORS["muted"], True,
                           bg=COLORS["card2"]).pack(anchor="w", padx=10, pady=(8, 1))
                self.label(cell, str(value), 12, color, True,
                           bg=COLORS["card2"]).pack(anchor="w", padx=10, pady=(0, 8))
            if not rows:
                self.empty_state(
                    results, "NO BOOKING HISTORY",
                    "No booking history found for the selected date or date range.")
                return
            table_card = self.card(results)
            table_card.pack(fill="both", expand=True)
            self.configure_customer_table_style()
            columns = ("id", "date", "time", "service", "vehicle", "bay",
                       "total", "paid", "balance", "status", "payment")
            tree = ttk.Treeview(table_card, columns=columns, show="headings",
                                style="Customer.Treeview", selectmode="browse",
                                height=10)
            headings = {
                "id": "Reservation", "date": "Booking Date", "time": "Time",
                "service": "Service", "vehicle": "Vehicle", "bay": "Bay",
                "total": "Total", "paid": "Verified Paid", "balance": "Balance",
                "status": "Booking Status", "payment": "Payment Status",
            }
            for column in columns:
                tree.heading(column, text=headings[column])
                tree.column(column, width=105 if column == "id" else 92,
                            anchor="center")
            tree.tag_configure("approved", foreground=RESERVATION_STATUS_COLORS["Approved"])
            tree.tag_configure("pending", foreground=RESERVATION_STATUS_COLORS["Pending"])
            tree.tag_configure("rejected", foreground=RESERVATION_STATUS_COLORS["Rejected"])
            tree.tag_configure("completed", foreground=RESERVATION_STATUS_COLORS["Completed"])
            tree.tag_configure("cancelled", foreground="#ff9f6b")
            for index, row in enumerate(rows):
                tag = str(row.get("status") or "").lower()
                tree.insert("", "end", iid=row["reservation_id"], values=(
                    "☐", row["reservation_id"], row["booking_date"], row["booking_time"],
                    row["service"], row["vehicle"], row["bay"], money(row["total"]),
                    money(row.get("verified_paid") or 0),
                    money(row.get("remaining_balance") or 0), row["status"],
                    row.get("payment_status") or "",
                ), tags=(tag, "odd" if index % 2 == 0 else "even"))
            tree.tag_configure("odd", background="#0b1624")
            tree.tag_configure("even", background="#101d2d")
            tree.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 0))
            table_card.rowconfigure(0, weight=1)
            table_card.columnconfigure(0, weight=1)
            v_scroll = ttk.Scrollbar(table_card, orient="vertical", command=tree.yview)
            h_scroll = ttk.Scrollbar(table_card, orient="horizontal", command=tree.xview)
            v_scroll.grid(row=0, column=1, sticky="ns", pady=(8, 0))
            h_scroll.grid(row=1, column=0, sticky="ew")
            tree.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)
            tree.bind("<Double-1>", lambda _event: self._history_view_booking(tree))
            def toggle_history_checkbox(event):
                if tree.identify("column", event.x) != "#1":
                    return
                item = tree.identify_row(event.y)
                if not item:
                    return
                selected = set(tree.selection())
                if item in selected:
                    selected.remove(item)
                else:
                    selected.add(item)
                tree.selection_set(tuple(selected))
                for iid in tree.get_children():
                    values = list(tree.item(iid, "values"))
                    values[0] = "☑" if iid in selected else "☐"
                    tree.item(iid, values=values)
                return "break"
            tree.bind("<Button-1>", toggle_history_checkbox, add="+")
            self.btn(results, "VIEW SELECTED BOOKING",
                     lambda: self._history_view_booking(tree)).pack(
                         anchor="w", pady=(8, 0))

        draw_history()

    def _history_view_booking(self, tree):
        selection = tree.selection()
        if selection:
            self.show_reservation_details(selection[0], customer_view=True)

    def _refund_status_color(self, status):
        return {
            "Pending": "#c084fc",
            "Under Review": COLORS["yellow"],
            "Processing": COLORS["cyan"],
            "Completed": COLORS["green"],
            "Rejected": "#ff4d4d",
        }.get(str(status or "Pending"), COLORS["cyan"])

    def _refund_payment_method_text(self, refund):
        methods = refund.get("payment_methods") or []
        cleaned = [str(method).strip() for method in methods if str(method).strip()]
        if cleaned:
            return ", ".join(cleaned)
        return refund.get("refund_method") or "Not provided"

    def _notification_reservation_id(self, notification):
        related_type = str(notification.get("related_type") or "").strip().lower()
        related_id = notification.get("related_id")
        if not related_id:
            return ""
        if related_type in ("reservation", "booking"):
            return str(related_id)
        if related_type == "payment":
            for record in self.store.data.get("payments", []):
                if str(record.get("payment_id")) == str(related_id):
                    return str(record.get("reservation_id") or "")
        if related_type == "refund":
            refund = self.store.get_refund(related_id)
            if refund:
                return str(refund.get("reservation_id") or "")
        return ""

    def show_customer_refunds(self):
        """Refund records are handled by staff; do not expose a customer refund-details page."""
        messagebox.showinfo(
            "Refund Assistance",
            "Please contact the carwash staff for updates about a refund."
        )
        self.show_customer_dashboard()

    def refresh_customer_refunds(self):
        self.store.load()
        self.show_customer_refunds()

    def refresh_customer_refund_details(self, refund_id):
        self.store.load()
        self.show_customer_refund_details(refund_id)

    def show_customer_refund_details(self, refund_id):
        """Customer-facing refund detail pages are intentionally removed."""
        messagebox.showinfo(
            "Refund Assistance",
            "Please contact the carwash staff for updates about a refund."
        )
        self.show_customer_dashboard()

    def show_customer_notifications(self):
        """Dedicated dashboard for the signed-in customer's notifications."""
        if not self.current_user:
            messagebox.showerror("Sign In Required", "Please log in to view notifications.")
            self.show_landing()
            return
        username = self.current_user["username"]
        content = self.customer_shell(
            "notifications",
            "Notifications",
            "Booking, payment, and refund updates for your account.",
        )
        rows = self.store.notifications_for_user(username)
        unread = sum(1 for row in rows if not row.get("read_at"))
        controls = tk.Frame(content, bg=COLORS["bg"])
        controls.pack(fill="x", pady=(0, 10))
        self.btn(controls, "REFRESH", self.refresh_customer_notifications,
                 primary=False, width=12).pack(side="left")
        if unread:
            self.btn(controls, "MARK ALL READ", self.mark_all_customer_notifications_read,
                     primary=False, width=16).pack(side="left", padx=8)
        self.label(controls, f"{unread} unread", 9, COLORS["yellow"], True,
                   bg=COLORS["bg"]).pack(side="right", padx=4)
        if not rows:
            card = self.card(content)
            card.pack(fill="x")
            self.label(card, "NO NOTIFICATIONS", 12, COLORS["cyan"], True).pack(
                anchor="w", padx=22, pady=(20, 4))
            self.label(card, "Booking, payment, and refund updates will appear here.",
                       9, COLORS["muted"]).pack(anchor="w", padx=22, pady=(0, 20))
            return
        for notification in rows:
            is_unread = not notification.get("read_at")
            card = self.card(content)
            card.pack(fill="x", pady=(0, 8))
            heading = tk.Frame(card, bg=COLORS["card"])
            heading.pack(fill="x", padx=20, pady=(12, 2))
            self.label(heading, notification.get("title") or "Notification", 11,
                       COLORS["yellow"] if is_unread else COLORS["white"], True).pack(side="left")
            self.label(heading, "UNREAD" if is_unread else "READ", 8,
                       COLORS["yellow"] if is_unread else COLORS["muted"], True).pack(side="right")
            self.label(card, notification.get("message") or "", 9, COLORS["muted"],
                       wraplength=620, justify="left").pack(anchor="w", padx=20, pady=(0, 2))
            reservation_id = self._notification_reservation_id(notification)
            meta = []
            if reservation_id:
                meta.append(f"Related Reservation ID: {reservation_id}")
            meta.append(format_timestamp(notification.get("created_at")))
            self.label(card, "     ".join(meta), 8, COLORS["muted"]).pack(
                anchor="w", padx=20, pady=(0, 6))
            actions = tk.Frame(card, bg=COLORS["card"])
            actions.pack(fill="x", padx=20, pady=(0, 12))
            self.btn(
                actions, "OPEN NOTIFICATION",
                lambda nid=notification.get("notification_id"):
                    self.show_customer_notification_detail(nid),
                width=20
            ).pack(side="right")

    def refresh_customer_notifications(self):
        self.store.load()
        self.show_customer_notifications()

    def mark_all_customer_notifications_read(self):
        if not self.current_user:
            self.show_landing()
            return
        self.store.mark_all_notifications_read(self.current_user["username"])
        self.show_customer_notifications()

    def show_customer_notification_detail(self, notification_id):
        """Open a notification and mark it read for the signed-in customer."""
        if not self.current_user:
            messagebox.showerror("Sign In Required", "Please log in to view notifications.")
            self.show_landing()
            return
        username = self.current_user["username"]
        notification = next((row for row in self.store.notifications_for_user(username)
                             if str(row.get("notification_id")) == str(notification_id)), None)
        if not notification:
            messagebox.showerror("Notification Not Found", "That notification could not be found.")
            self.show_customer_notifications()
            return
        if not notification.get("read_at"):
            self.store.mark_notification_read(notification_id, username)
            notification = next((row for row in self.store.notifications_for_user(username)
                                 if str(row.get("notification_id")) == str(notification_id)),
                                notification)
        content = self.customer_shell(
            "notifications",
            "Notification",
            notification.get("title") or "Notification",
        )
        panel = self.card(content)
        panel.pack(fill="x", pady=(0, 12))
        self.label(panel, "NOTIFICATION DETAILS", 13, COLORS["cyan"], True).pack(
            anchor="w", padx=20, pady=(18, 8))
        self.label(panel, notification.get("title") or "Notification", 14,
                   COLORS["white"], True, wraplength=620, justify="left").pack(
                       anchor="w", padx=20, pady=(0, 6))
        self.label(panel, notification.get("message") or "", 10, COLORS["white"],
                   wraplength=620, justify="left").pack(anchor="w", padx=20, pady=(0, 10))
        rows = []
        reservation_id = self._notification_reservation_id(notification)
        if reservation_id:
            rows.append(("Related Reservation ID", reservation_id))
        rows.append(("Date and Time", format_timestamp(notification.get("created_at"))))
        rows.append(("Notification Status", "Read" if notification.get("read_at") else "Unread"))
        self.detail_rows(panel, rows, label_width=24)
        controls = tk.Frame(content, bg=COLORS["bg"])
        controls.pack(fill="x", pady=(0, 12))
        self.btn(controls, "BACK TO NOTIFICATIONS", self.show_customer_notifications,
                 width=22).pack(side="left")

    def show_account_settings(self):
        """Account settings for the currently authenticated user only."""
        if not self.current_user:
            messagebox.showerror("Sign In Required", "Please log in to view account settings.")
            self.show_landing()
            return
        if self.current_user.get("role") != "customer":
            messagebox.showinfo("Account Settings", "Account settings are available from the customer dashboard.")
            return
        content = self.customer_shell(
            "account",
            "My Account",
            "View your account information and securely change your password.",
        )
        panel = self.card(content)
        panel.pack(fill="x", pady=(0, 12))
        self.label(panel, "ACCOUNT SETTINGS", 13, COLORS["cyan"], True).pack(
            anchor="w", padx=20, pady=(18, 8))
        self.label(panel, "Your account is private. Only your own account information is shown here.",
                   9, COLORS["muted"]).pack(anchor="w", padx=20, pady=(0, 14))
        actions = tk.Frame(panel, bg=COLORS["card"])
        actions.pack(fill="x", padx=20, pady=(0, 20))
        self.btn(actions, "VIEW ACCOUNT INFORMATION", self.view_account_information,
                 width=28).pack(side="left", padx=(0, 8), pady=4)
        self.btn(actions, "CHANGE PASSWORD", self.show_change_password,
                 primary=False, width=22).pack(side="left", padx=8, pady=4)

    def view_account_information(self):
        """Show only the current user's saved account fields; never reveal a password hash."""
        if not self.current_user:
            messagebox.showerror("Sign In Required", "Please log in first.")
            return
        user = self.store.user(self.current_user["username"])
        if not user:
            messagebox.showerror("Account Not Found", "Your account could not be found.")
            return
        dialog = tk.Toplevel(self)
        dialog.title("View Account Information")
        dialog.configure(bg=COLORS["bg"])
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        outer = self.card(dialog)
        outer.pack(fill="both", expand=True, padx=18, pady=18)
        self.label(outer, "ACCOUNT INFORMATION", 13, COLORS["cyan"], True).pack(anchor="w", padx=20, pady=(18, 12))
        details = [
            ("Full Name", user["full_name"]),
            ("Username", user["username"]),
            ("Contact", user["contact"]),
            ("Role", user["role"].title()),
            ("Password", "••••••••"),
        ]
        for field_name, value in details:
            line = tk.Frame(outer, bg=COLORS["card"])
            line.pack(fill="x", padx=20, pady=5)
            self.label(line, f"{field_name}:", 10, COLORS["muted"], True, width=14, anchor="w").pack(side="left")
            self.label(line, str(value), 10, COLORS["white"], True, anchor="w").pack(side="left", padx=(8, 0))
            if field_name == "Password":
                self.btn(
                    line,
                    self.explain_password_visibility,
                    primary=False,
                    width=16
                ).pack(side="left", padx=(12, 0))

        controls = tk.Frame(outer, bg=COLORS["card"])
        controls.pack(anchor="w", padx=20, pady=(15, 20))
        self.btn(controls, "CHANGE PASSWORD", lambda: (dialog.destroy(), self.show_change_password()), primary=False, width=18).pack(side="left", padx=(0, 8))
        self.btn(controls, "CLOSE", dialog.destroy, primary=False, width=10).pack(side="left")

    def show_refund_profile_form(self):
        """Legacy customer refund-profile UI is removed; stored data remains intact."""
        messagebox.showinfo(
            "Refund Assistance",
            "Refund destination details are managed through the dedicated refund workflow."
        )
        self.show_account_settings()

    def explain_password_visibility(self):
        """Explain why an existing hashed password cannot be revealed."""
        messagebox.showinfo(
            "Password Security",
            "Your current password is stored as a secure one-way hash, so the original password cannot be displayed. "
            "You can change it using CHANGE PASSWORD. In that form, use SHOW PASSWORDS to view the password you are typing.",
            parent=self
        )

    def show_change_password(self):
        """Open a password-change dialog with backend verification and secure hashing."""
        if not self.current_user:
            messagebox.showerror("Sign In Required", "Please log in first.")
            return
        username = self.current_user["username"]
        dialog = tk.Toplevel(self)
        dialog.title("Change Password")
        dialog.configure(bg=COLORS["bg"])
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        panel = self.card(dialog)
        panel.pack(fill="both", expand=True, padx=18, pady=18)
        self.label(panel, "CHANGE PASSWORD", 14, COLORS["cyan"], True).pack(anchor="w", padx=22, pady=(18, 6))
        self.label(panel, "Use at least 6 characters. Your new password will be securely hashed.", 9, COLORS["muted"]).pack(anchor="w", padx=22, pady=(0, 14))
        entries = {}
        for field_name in ("Current Password", "New Password", "Confirm New Password"):
            self.label(panel, field_name, 10, COLORS["white"], True).pack(anchor="w", padx=22, pady=(7, 3))
            entry = tk.Entry(panel, show="*", font=("Segoe UI", 10), relief="flat", width=34)
            entry.pack(fill="x", padx=22, ipady=7)
            entries[field_name] = entry

        show_passwords = tk.BooleanVar(value=False)
        def toggle_password_visibility():
            visible = not show_passwords.get()
            show_passwords.set(visible)
            mask = "" if visible else "*"
            for password_entry in entries.values():
                password_entry.configure(show=mask)
            visibility_button.configure(
                text="HIDE PASSWORDS" if visible else "SHOW PASSWORDS"
            )

        visibility_button = self.btn(
            panel, "SHOW PASSWORDS", toggle_password_visibility, primary=False, width=18
        )
        visibility_button.pack(anchor="w", padx=22, pady=(8, 2))

        def save_password():
            current = entries["Current Password"].get()
            new = entries["New Password"].get()
            confirm = entries["Confirm New Password"].get()
            if not current or not new or not confirm:
                messagebox.showwarning("Missing Information", "Complete all password fields.", parent=dialog)
                return
            if new != confirm:
                messagebox.showerror("Password Mismatch", "The new password and confirmation do not match.", parent=dialog)
                return
            if len(new.strip()) < 6:
                messagebox.showwarning("Weak Password", "Use a password with at least 6 characters.", parent=dialog)
                return
            ok, message = self.store.change_password(username, current, new)
            if not ok:
                messagebox.showerror("Password Not Changed", message, parent=dialog)
                return
            messagebox.showinfo("Password Changed", message, parent=dialog)
            dialog.destroy()
            refreshed = self.store.user(username)
            if refreshed:
                self.current_user = dict(refreshed)

        controls = tk.Frame(panel, bg=COLORS["card"])
        controls.pack(fill="x", padx=22, pady=18)
        self.btn(controls, "SAVE NEW PASSWORD", save_password, width=20).pack(side="left", padx=(0, 8))
        self.btn(controls, "CANCEL", dialog.destroy, primary=False, width=12).pack(side="left")
        entries["Current Password"].focus_set()

    def customer_reservation_action(self, tree):
        selection = tree.selection()
        if not selection:
            messagebox.showinfo("Select Booking", "Choose a reservation first.")
            return
        self.show_reservation_details(selection[0], customer_view=True)

    def show_booking(self):
        self.clear()
        self.header("Create Booking", "Your booking request will remain Pending until reviewed by the admin.")
        outer = tk.Frame(self.page, bg=COLORS["bg"])
        outer.pack(fill="both", expand=True, padx=35, pady=5)
        left = self.card(outer); left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        right = self.card(outer); right.pack(side="right", fill="both", expand=True, padx=(8, 0))
        values = {}
        self.label(left, "VEHICLE", 11, COLORS["cyan"], True).pack(anchor="w", padx=22, pady=(18, 4))
        values["vehicle"] = tk.StringVar(value=self.selected_vehicle)
        vehicle = ttk.Combobox(left, textvariable=values["vehicle"], values=VEHICLES, state="readonly")
        vehicle.pack(fill="x", padx=22)
        self.label(left, "SERVICE", 11, COLORS["cyan"], True).pack(anchor="w", padx=22, pady=(14, 4))
        initial_vehicle = values["vehicle"].get()
        initial_services = service_names_for_vehicle(initial_vehicle)
        if not initial_services:
            initial_vehicle = VEHICLES[0]
            values["vehicle"].set(initial_vehicle)
            initial_services = service_names_for_vehicle(initial_vehicle)
        initial_service = initial_services[0]
        values["service"] = tk.StringVar(value=f"{initial_service} — {money(PRICES[initial_service][initial_vehicle])}")
        service = ttk.Combobox(left, textvariable=values["service"], values=service_options(initial_vehicle), state="readonly")
        service.pack(fill="x", padx=22)
        service_price_label = self.label(
            left, f"Service price: {money(PRICES[service_name(values['service'].get())][values['vehicle'].get()])}",
            9, COLORS["cyan"], True
        )
        service_price_label.pack(anchor="w", padx=22, pady=(4, 0))
        self.label(left, "RESERVATION DATE", 11, COLORS["cyan"], True).pack(anchor="w", padx=22, pady=(14, 4))
        date_row = tk.Frame(left, bg=COLORS["card"]); date_row.pack(fill="x", padx=22)
        values["date"] = tk.StringVar(value=business_now().date().strftime("%m/%d/%Y"))
        date_entry = tk.Entry(date_row, textvariable=values["date"], font=("Segoe UI", 10), relief="flat")
        date_entry.pack(side="left", fill="x", expand=True, ipady=7)
        def choose_date():
            try:
                from tkcalendar import Calendar
            except ImportError:
                messagebox.showinfo("Calendar dependency", "Install the calendar widget with:\n\npython -m pip install tkcalendar")
                return
            popup = tk.Toplevel(self); popup.title("Choose reservation date"); popup.configure(bg=COLORS["card"])
            cal = Calendar(popup, selectmode="day", date_pattern="mm/dd/yyyy", mindate=business_now().date(), showweeknumbers=False)
            cal.pack(padx=12, pady=12)
            def choose():
                values["date"].set(cal.get_date()); popup.destroy()
            self.btn(popup, "SELECT DATE", choose).pack(pady=(0, 12))
            popup.transient(self); popup.grab_set()
        self.btn(date_row, "CALENDAR", choose_date, primary=False).pack(side="left", padx=(7, 0))
        self.label(left, "TIME", 11, COLORS["cyan"], True).pack(anchor="w", padx=22, pady=(14, 4))
        values["time"] = tk.StringVar(value=TIMES[0])
        time_grid = tk.Frame(left, bg=COLORS["card"])
        time_grid.pack(fill="x", padx=22, pady=(2, 4))
        time_buttons = {}

        def selected_date_text():
            try:
                return datetime.strptime(values["date"].get().strip(), "%m/%d/%Y").strftime("%m/%d/%Y")
            except ValueError:
                return ""

        def time_is_available(time_value):
            date_text = selected_date_text()
            return bool(date_text) and slot_is_future(date_text, time_value)

        def refresh_time_buttons(*_):
            date_text = selected_date_text()
            for name, button in time_buttons.items():
                future = bool(date_text) and time_is_available(name)
                selected = values["time"].get() == name
                button.configure(
                    state="normal" if future else "disabled",
                    bg=(COLORS["cyan"] if selected else COLORS["button2"]) if future else "#3b4652",
                    fg="white" if future else "#9aa4ad",
                    activebackground=COLORS["cyan"] if future else "#3b4652",
                    cursor="hand2" if future else "arrow",
                    highlightthickness=2 if selected and future else 0,
                    highlightbackground=COLORS["cyan"])

        def select_time(time_value):
            if not time_is_available(time_value):
                messagebox.showwarning("Time Unavailable", "This reservation time has already passed. Please select a future time slot.")
                values["time"].set("")
                refresh_time_buttons()
                refresh_bay_buttons()
                return
            values["time"].set(time_value)
            refresh_time_buttons()
            refresh_bay_buttons()

        for index, time_value in enumerate(TIMES):
            button = tk.Button(
                time_grid, text=time_value,
                command=lambda value=time_value: select_time(value),
                font=("Segoe UI", 9, "bold"), fg="white", bg=COLORS["button2"],
                activebackground=COLORS["cyan"], activeforeground="white",
                relief="flat", bd=0, cursor="hand2", pady=7, highlightthickness=0)
            button.grid(row=index // 2, column=index % 2, sticky="ew", padx=(0, 5), pady=3)
            time_buttons[time_value] = button
        time_grid.columnconfigure(0, weight=1)
        time_grid.columnconfigure(1, weight=1)

        price_label = self.label(left, "Total: --", 15, COLORS["cyan"], True)
        price_label.pack(anchor="w", padx=22, pady=16)
        self.label(right, "CARWASH BAY", 11, COLORS["cyan"], True).pack(anchor="w", padx=20, pady=(14, 4))
        values["bay"] = tk.StringVar(value=BAYS[0])
        bay_grid = tk.Frame(right, bg=COLORS["card"])
        bay_grid.pack(fill="x", padx=20, pady=(2, 4))
        bay_buttons = {}

        def refresh_bay_buttons(*_):
            date_text = selected_date_text()
            selected_still_available = False
            for bay_name, button in bay_buttons.items():
                available = bool(date_text) and bool(values["time"].get()) and time_is_available(values["time"].get()) and self.store.bay_available(
                    bay_name, date_text, values["time"].get())
                selected = values["bay"].get() == bay_name
                if selected and available:
                    selected_still_available = True
                button.configure(text=bay_name,
                                 bg=(COLORS["cyan"] if selected else COLORS["button2"]) if available else "#3b4652",
                                 fg="white" if available else "#9aa4ad",
                                 state="normal" if available else "disabled",
                                 activebackground=COLORS["cyan"] if available else "#3b4652",
                                 cursor="hand2" if available else "arrow",
                                 highlightthickness=2 if selected and available else 0,
                                 highlightbackground=COLORS["cyan"])
            if not selected_still_available and values["bay"].get():
                values["bay"].set("")

        def select_bay(bay_name):
            date_text = selected_date_text()
            if not date_text or not values["time"].get() or not time_is_available(values["time"].get()) or not self.store.bay_available(bay_name, date_text, values["time"].get()):
                messagebox.showwarning("Bay Unavailable", "This bay is not available for the selected date and time. Please choose another available slot.")
                refresh_bay_buttons()
                return
            values["bay"].set(bay_name)
            refresh_bay_buttons()

        def rebuild_bay_buttons():
            for child in bay_grid.winfo_children():
                child.destroy()
            bay_buttons.clear()
            for index, bay_name in enumerate(BAYS):
                button = tk.Button(
                    bay_grid, text=bay_name,
                    command=lambda name=bay_name: select_bay(name),
                    font=("Segoe UI", 9, "bold"), fg="white", bg=COLORS["button2"],
                    activebackground=COLORS["cyan"], activeforeground="white",
                    relief="flat", bd=0, cursor="hand2", pady=7, highlightthickness=0)
                button.grid(row=index // 2, column=index % 2, sticky="ew", padx=(0, 5), pady=3)
                bay_buttons[bay_name] = button
            bay_grid.columnconfigure(0, weight=1)
            bay_grid.columnconfigure(1, weight=1)
            refresh_bay_buttons()
            if values["bay"].get() in bay_buttons:
                select_bay(values["bay"].get())

        rebuild_bay_buttons()
        initial_date = selected_date_text()
        first_future = next((slot for slot in TIMES if initial_date and time_is_available(slot)), "")
        values["time"].set(first_future)
        refresh_time_buttons()
        refresh_bay_buttons()

        preview = tk.Label(right, text="", justify="left", anchor="nw", font=("Segoe UI", 11), fg="#d9e6ef", bg=COLORS["card2"], padx=18, pady=18)
        preview.pack(fill="both", expand=True, padx=20, pady=20)
        def refresh_service_dropdown(*_):
            vehicle_name = values["vehicle"].get()
            available_services = service_names_for_vehicle(vehicle_name)
            options = service_options(vehicle_name)
            service.configure(values=options)
            current_service = service_name(values["service"].get()) if values["service"].get() else ""
            if current_service not in available_services:
                current_service = available_services[0] if available_services else ""
            if not current_service:
                values["service"].set("")
                service_price_label.config(text="No services available for this vehicle.")
                return
            values["service"].set(f"{current_service} — {money(PRICES[current_service][vehicle_name])}")
            update_preview()

        def update_preview(*_):
            v, s = values["vehicle"].get(), service_name(values["service"].get())
            if s not in PRICES or v not in PRICES.get(s, {}):
                service_price_label.config(text="Select a valid service for this vehicle.")
                price_label.config(text="Total: —")
                preview.config(text=f"INVOICE:\n\nVehicle: {v}\nPlease select an available service.")
                refresh_bay_buttons()
                return
            total = PRICES[s][v]
            dp = round(total * DOWNPAYMENT_RATE, 2)
            service_price_label.config(text=f"Service price: {money(total)}")
            price_label.config(text=f"Total: {money(total)}")
            preview.config(text=f"INVOICE:\n\nVehicle: {v}\nService: {s}\nDate: {values['date'].get()}\nTime: {values['time'].get()}\n{values['bay'].get()}\n\nTotal: {money(total)}\nRequired 30% downpayment: {money(dp)}\nRemaining balance after downpayment: {money(total-dp)}\n\nBooking status after submission: PENDING")
            refresh_bay_buttons()
        vehicle.bind("<<ComboboxSelected>>", refresh_service_dropdown)
        def on_date_or_time_change(*_):
            date_text = selected_date_text()
            if date_text:
                try:
                    chosen_day = datetime.strptime(date_text, "%m/%d/%Y").date()
                except ValueError:
                    chosen_day = None
                if chosen_day and chosen_day < business_now().date():
                    values["time"].set("")
                elif values["time"].get() and not time_is_available(values["time"].get()):
                    values["time"].set("")
            refresh_time_buttons()
            refresh_bay_buttons()
            update_preview()
        values["date"].trace_add("write", on_date_or_time_change)
        values["time"].trace_add("write", update_preview)
        values["bay"].trace_add("write", update_preview)
        values["vehicle"].trace_add("write", update_preview)
        values["service"].trace_add("write", update_preview)
        update_preview()
        def periodic_availability_check():
            if not self.winfo_exists():
                return
            if not time_is_available(values["time"].get()) if values["time"].get() else False:
                values["time"].set("")
            refresh_time_buttons()
            refresh_bay_buttons()
            self.after(15000, periodic_availability_check)
        self.after(15000, periodic_availability_check)
        def submit():
            try:
                chosen = datetime.strptime(values["date"].get().strip(), "%m/%d/%Y").date()
            except ValueError:
                messagebox.showerror("Invalid Date", "Choose a date using the calendar or MM/DD/YYYY format.")
                return
            now = business_now()
            if chosen < now.date():
                messagebox.showerror("Invalid Date", "Reservation date cannot be in the past.")
                return
            v, s, t, b = values["vehicle"].get(), service_name(values["service"].get()), values["time"].get(), values["bay"].get()
            date_text = chosen.strftime("%m/%d/%Y")
            if not t or not slot_is_future(date_text, t, business_now()):
                messagebox.showerror("Time Expired", "This reservation time has already passed. Please select a future time slot.")
                refresh_time_buttons()
                refresh_bay_buttons()
                return
            if s not in PRICES or v not in PRICES.get(s, {}):
                messagebox.showerror("Invalid Service", "Please select a service available for the selected vehicle.")
                return
            if not b:
                messagebox.showerror("Select Bay", "Please select an available carwash bay.")
                return
            if not self.store.bay_available(b, date_text, t):
                messagebox.showerror("Bay Unavailable", f"{b} is unavailable at {t} on {date_text}. Please choose another available bay.")
                refresh_bay_buttons()
                return
            total = PRICES[s][v]
            try:
                rid = self.store.create_reservation({"username": self.current_user["username"],
                    "customer": self.current_user["full_name"], "contact": self.current_user["contact"],
                    "vehicle": v, "service": s, "date": date_text, "time": t, "bay": b, "total": total})
            except ValueError as exc:
                messagebox.showerror("Reservation Not Available", str(exc))
                refresh_time_buttons()
                refresh_bay_buttons()
                return
            messagebox.showinfo("Booking Submitted", f"Your booking request {rid} has been submitted.\nStatus: PENDING")
            self.show_reservation_details(rid, customer_view=True)
        bottom = tk.Frame(self.page, bg=COLORS["bg"]); bottom.pack(fill="x", padx=35, pady=(0, 12))
        self.btn(bottom, "SUBMIT BOOKING REQUEST", submit, width=25).pack(side="right")
        self.btn(bottom, "BACK TO DASHBOARD", self.show_customer_dashboard, primary=False).pack(side="left")

    def detail_rows(self, parent, rows, label_width=29, status_colors=None):
        """Render name/value pairs in an aligned grid so both detail cards line up."""
        grid = tk.Frame(parent, bg=COLORS["card"])
        grid.pack(fill="x", padx=20, pady=(0, 8))
        grid.grid_columnconfigure(1, weight=1)
        value_labels = []
        name_req = 0
        for row_index, (name, value) in enumerate(rows):
            name_label = self.label(grid, name + ":", 10, COLORS["muted"], True, width=label_width,
                                    anchor="w")
            name_label.grid(row=row_index, column=0, sticky="w", padx=(0, 8), pady=2)
            name_req = max(name_req, name_label.winfo_reqwidth())
            color = COLORS["white"]
            value_bg = COLORS["card"]
            badge_status = "status" in name.lower() and str(value) in RESERVATION_STATUS_COLORS
            if "status" in name.lower():
                color = COLORS["cyan"]
                if status_colors:
                    color = status_colors.get(str(value), COLORS["cyan"])
            if badge_status:
                color = "#FFFFFF"
                value_bg = RESERVATION_STATUS_COLORS[str(value)]
            value_label = self.label(grid, str(value), 10, color, True, bg=value_bg, anchor="w", wraplength=190)
            value_label.grid(row=row_index, column=1, sticky="w", pady=2, padx=(0, 4))
            value_labels.append(value_label)
        return value_labels, name_req

    def payment_reference_preview(self, parent, payments, rid, customer_view=False):
        """Show the stored payment receipt at the bottom of the Payment Details card."""
        image_payment = next((p for p in payments if self.store.payment_image_path(p)), None)
        image_path = self.store.payment_image_path(image_payment) if image_payment else None
        wrapper = tk.Frame(parent, bg=COLORS["card"])
        wrapper.pack(fill="x", padx=20, pady=(6, 12))
        self.label(wrapper, "PAYMENT REFERENCE IMAGE", 10, COLORS["cyan"], True).pack(
            anchor="w", pady=(0, 2))
        if image_payment and image_path:
            reference_name = (image_payment.get("reference_image_name")
                              or image_path.name or "Uploaded image")
            self.label(wrapper,
                       f"PAY-{int(image_payment['payment_id']):03d}  •  {reference_name}",
                       9, COLORS["muted"], wraplength=260, justify="left").pack(
                           anchor="w", pady=(0, 6))
            photo = load_payment_image(image_path, max_width=260, max_height=220)
            if photo is not None:
                preview = tk.Label(wrapper, image=photo, bg=COLORS["card2"],
                                   highlightbackground="#23374e", highlightthickness=1)
                preview.image = photo
                preview.pack(anchor="w", pady=(0, 6))
            else:
                self.label(wrapper, f"Image file: {image_path.name} (preview unavailable)",
                           9, COLORS["muted"]).pack(anchor="w", pady=(0, 6))
            controls = tk.Frame(wrapper, bg=COLORS["card"])
            controls.pack(anchor="w")
            self.btn(controls, "VIEW FULL IMAGE",
                     lambda p=image_payment: self.show_payment_image(p), width=15).pack(
                         side="left", padx=(0, 6))
            self.btn(controls, "OPEN IMAGE FILE",
                     lambda path=image_path: self.open_payment_image_file(path),
                     primary=False, width=15).pack(side="left")
            return
        self.label(wrapper, "No payment reference image has been uploaded for this reservation yet.",
                   9, COLORS["muted"], wraplength=260, justify="left").pack(
                       anchor="w", pady=(0, 4))
        reservation = self.store.get_reservation(rid)
        can_upload = (customer_view and reservation
                      and reservation.get("status") not in ("Rejected", "Completed", "Cancelled")
                      and self.store.available_to_pay(rid) > 0)
        if can_upload:
            self.btn(wrapper, "UPLOAD PAYMENT REFERENCE IMAGE",
                     lambda: self.show_payment(rid), width=29).pack(anchor="w", pady=(4, 0))

    def downpayment_error_label(self, parent, eligibility, wraplength=480):
        """Return a red eligibility error label for the disabled 30% option."""
        if not eligibility or eligibility.get("downpayment_available"):
            return None
        message = eligibility.get("downpayment_message") or ""
        if not message:
            return None
        return self.label(parent, message, 9, ERROR_COLOR, True,
                          wraplength=wraplength, justify="left")

    def refund_details_card(self, parent, reservation, refund, customer_view=False):
        """Refund details are not displayed on reservation pages."""
        return None

    def refund_dashboard_section(self, parent, refunds):
        """Refund summaries are not displayed on the customer dashboard."""
        return None

    def notification_dashboard_section(self, parent):
        """Show the signed-in customer's latest private notifications."""
        if not self.current_user:
            return
        username = self.current_user["username"]
        rows = self.store.notifications_for_user(username)
        if not rows:
            return
        unread = sum(1 for row in rows if not row.get("read_at"))
        card = self.card(parent)
        card.pack(fill="x", padx=30, pady=(12, 0))
        heading = tk.Frame(card, bg=COLORS["card"])
        heading.pack(fill="x", padx=20, pady=(12, 5))
        self.label(heading, "NOTIFICATIONS", 12, COLORS["cyan"], True).pack(side="left")
        self.label(heading, f"{unread} unread", 9, COLORS["yellow"], True).pack(
            side="left", padx=(10, 0))
        if unread:
            self.btn(heading, "MARK ALL READ",
                     lambda: (self.store.mark_all_notifications_read(username),
                              self.show_customer_dashboard()),
                     primary=False, width=15).pack(side="right")
        for notification in rows[:4]:
            line = tk.Frame(card, bg=COLORS["card"])
            line.pack(fill="x", padx=20, pady=4)
            title_color = COLORS["yellow"] if not notification.get("read_at") else COLORS["white"]
            self.label(line, notification.get("title") or "Notification", 10,
                       title_color, True).pack(anchor="w")
            self.label(line, notification.get("message") or "", 9, COLORS["muted"],
                       wraplength=720, justify="left").pack(anchor="w")
            self.label(line, format_timestamp(notification.get("created_at")),
                       8, COLORS["muted"]).pack(anchor="w")
            if not notification.get("read_at"):
                self.btn(line, "MARK READ",
                         lambda nid=notification.get("notification_id"): (
                             self.store.mark_notification_read(nid, username),
                             self.show_customer_dashboard()),
                         primary=False, width=12).pack(anchor="e")

    def show_refund_details_form(self, rid):
        """Open the customer refund destination form for their own eligible reservation."""
        if not self.current_user or self.current_user.get("role") != "customer":
            messagebox.showerror("Access Denied", "Please sign in with a customer account.")
            return
        username = self.current_user.get("username")
        reservation = self.store.get_reservation(rid)
        if not reservation or reservation.get("username") != username:
            messagebox.showerror("Reservation Not Found",
                                 "You can only submit refund details for your own reservation.")
            return
        if reservation.get("status") not in ("Cancellation Requested", "Cancelled", "Rejected"):
            messagebox.showwarning("Refund Not Available",
                                   "Refund details are available after a reservation cancellation or rejection.")
            return
        if self.store._verified_total(rid) <= 0.005:
            messagebox.showinfo("No Confirmed Payment",
                                "No verified payment was found for this reservation.")
            return

        existing = self.store.refund_for_reservation(rid)
        if existing and existing.get("status") == "Completed":
            messagebox.showinfo("Refund Completed", "This refund has already been completed.")
            self.show_reservation_details(rid, customer_view=True)
            return

        self.clear()
        self.header("Refund Details", f"Reservation ID: {rid}")
        page = self.page
        nav = tk.Frame(page, bg=COLORS["bg"])
        nav.pack(fill="x", padx=24, pady=(0, 8))
        self.btn(nav, "BACK TO RESERVATION",
                 lambda: self.show_reservation_details(rid, customer_view=True),
                 primary=False).pack(side="left")

        card = self.card(page)
        card.pack(fill="x", padx=24, pady=10, anchor="n")
        self.label(card, "Refund Details Form", 16, COLORS["white"], True).pack(
            anchor="w", padx=20, pady=(18, 4))
        self.label(card, "Enter the account where your approved refund should be sent.",
                   10, COLORS["muted"]).pack(anchor="w", padx=20, pady=(0, 16))

        form = tk.Frame(card, bg=COLORS["card"])
        form.pack(fill="x", padx=20, pady=(0, 18))
        form.grid_columnconfigure(1, weight=1)
        saved = self.store.user(username) or {}
        method_var = tk.StringVar(value=(existing or {}).get("refund_method")
                                  or saved.get("refund_method") or "GCash")
        holder_var = tk.StringVar(value=(existing or {}).get("account_holder")
                                  or saved.get("refund_account_name") or "")
        number_var = tk.StringVar(value=(existing or {}).get("account_number")
                                  or saved.get("refund_account_number") or "")
        bank_var = tk.StringVar(value=(existing or {}).get("bank_name")
                                or saved.get("refund_bank_name") or "")

        self.label(form, "Refund Method", 10, COLORS["white"], True).grid(
            row=0, column=0, sticky="w", padx=(0, 16), pady=8)
        method_box = ttk.Combobox(form, textvariable=method_var,
                                  values=REFUND_METHODS, state="readonly", width=28)
        method_box.grid(row=0, column=1, sticky="ew", pady=8)
        self.label(form, "Account Holder Name", 10, COLORS["white"], True).grid(
            row=1, column=0, sticky="w", padx=(0, 16), pady=8)
        holder_entry = tk.Entry(form, textvariable=holder_var, font=("Segoe UI", 10),
                                bg=COLORS["card2"], fg=COLORS["white"],
                                insertbackground=COLORS["white"], relief="flat")
        holder_entry.grid(row=1, column=1, sticky="ew", ipady=8, pady=8)
        self.label(form, "Account Number", 10, COLORS["white"], True).grid(
            row=2, column=0, sticky="w", padx=(0, 16), pady=8)
        number_entry = tk.Entry(form, textvariable=number_var, font=("Segoe UI", 10),
                                bg=COLORS["card2"], fg=COLORS["white"],
                                insertbackground=COLORS["white"], relief="flat")
        number_entry.grid(row=2, column=1, sticky="ew", ipady=8, pady=8)
        bank_label = self.label(form, "Bank Name", 10, COLORS["white"], True)
        bank_entry = tk.Entry(form, textvariable=bank_var, font=("Segoe UI", 10),
                              bg=COLORS["card2"], fg=COLORS["white"],
                              insertbackground=COLORS["white"], relief="flat")

        def update_method_fields(*_):
            if method_var.get() == "Bank Transfer":
                bank_label.grid(row=3, column=0, sticky="w", padx=(0, 16), pady=8)
                bank_entry.grid(row=3, column=1, sticky="ew", ipady=8, pady=8)
            else:
                bank_label.grid_forget()
                bank_entry.grid_forget()
        method_var.trace_add("write", update_method_fields)
        update_method_fields()

        def submit():
            method = method_var.get().strip()
            holder = holder_var.get().strip()
            account_number = number_var.get().strip()
            bank_name = bank_var.get().strip() if method == "Bank Transfer" else ""
            problem = refund_account_error(method, holder, account_number, bank_name)
            if problem:
                messagebox.showerror("Invalid Refund Details", problem, parent=self)
                return
            # Create/submit the request first when one does not yet exist.
            current_refund = self.store.refund_for_reservation(rid)
            if (reservation.get("status") in ("Cancellation Requested", "Cancelled")
                    and (not current_refund or not (
                        current_refund.get("requested_by_customer")
                        or current_refund.get("request_submitted_at")
                        or current_refund.get("customer_request")))):
                ok, message = self.store.submit_customer_refund_request(rid, username)
                if not ok:
                    messagebox.showerror("Refund Request Not Submitted", message, parent=self)
                    return
            ok, message = self.store.update_refund_details(
                rid, username, method, holder, account_number, bank_name)
            if not ok:
                messagebox.showerror("Refund Details Not Saved", message, parent=self)
                return
            messagebox.showinfo("Refund Details Submitted",
                                "Your refund details have been saved successfully. Your refund is pending review.",
                                parent=self)
            self.show_reservation_details(rid, customer_view=True)

        controls = tk.Frame(card, bg=COLORS["card"])
        controls.pack(fill="x", padx=20, pady=(0, 20))
        self.btn(controls, "SUBMIT REFUND DETAILS", submit, width=24).pack(side="left")
        self.btn(controls, "CANCEL", lambda: self.show_reservation_details(
            rid, customer_view=True), primary=False, width=12).pack(side="left", padx=10)

    def admin_update_refund(self, refund_id, rid):
        """Open the admin form for reviewing and recording a manually processed refund."""
        if not self.current_user or self.current_user.get("role") != "admin":
            messagebox.showerror("Access Denied", "Only an admin can update a refund.")
            return
        refund = self.store.get_refund(refund_id)
        if not refund:
            messagebox.showerror("Refund Not Found", "That refund request could not be found.")
            return
        dialog = tk.Toplevel(self)
        dialog.title("Record Refund")
        dialog.configure(bg=COLORS["bg"])
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        panel = self.card(dialog)
        panel.pack(fill="both", expand=True, padx=18, pady=18)
        self.label(panel, "RECORD REFUND", 14, COLORS["cyan"], True).pack(
            anchor="w", padx=22, pady=(18, 5))
        self.label(panel, f"{refund_id}  •  Reservation {rid}", 9,
                   COLORS["muted"], True).pack(anchor="w", padx=22, pady=(0, 10))
        refund_method = str(refund.get("refund_method") or "").strip()
        number_label = "GCash Number" if refund_method.lower() == "gcash" else "Bank Transfer Number"
        destination = [
            ("Refund Method", refund_method or "Not provided"),
            ("Bank Name", refund.get("bank_name") or "N/A"),
            ("Account Holder", refund.get("account_holder") or "Not provided"),
            (number_label, refund.get("account_number") or "Not provided"),
        ]
        for field_name, value in destination:
            self.label(panel, f"{field_name}: {value}", 9, COLORS["white"],
                       True).pack(anchor="w", padx=22, pady=1)

        form = tk.Frame(panel, bg=COLORS["card"])
        form.pack(fill="x", padx=22, pady=(12, 0))
        # Saving this admin refund form marks the refund as Completed.
        # The refund date is recorded automatically by update_refund_status.
        selected_status = "Completed"
        # Pre-fill the net refund for fully paid cancelled reservations after the 30% deduction.
        reservation_record = self.store.get_reservation(rid)
        verified_payments = [p for p in self.store.payments_for_reservation(rid)
                             if p.get("verification_status") == "Verified"]
        confirmed_paid = round(sum(float(p.get("amount_submitted") or 0)
                                   for p in verified_payments), 2)
        total_amount = float((reservation_record or {}).get("total") or 0)
        cancellation_deduction = self.store.cancellation_fee_for_reservation(reservation_record)
        default_refund_amount = float(refund.get("amount") or 0)
        if cancellation_deduction > 0:
            default_refund_amount = cancellation_refund_amount(total_amount, confirmed_paid)
        self.label(form, f"Total Booking Price (PHP): {money(total_amount)}", 9, COLORS["muted"], True).pack(anchor="w", pady=(0, 2))
        self.label(form, f"Actual Amount Paid (PHP): {money(confirmed_paid)}", 9, COLORS["muted"], True).pack(anchor="w", pady=2)
        self.label(form, f"Non-Refundable Cancellation Fee (PHP): {money(cancellation_deduction)}", 9, COLORS["yellow"], True).pack(anchor="w", pady=2)
        amount_var = tk.StringVar(value=f"{default_refund_amount:.2f}" if default_refund_amount else "0.00")

        self.label(form, "Refund Amount (PHP)", 9, COLORS["white"], True).pack(anchor="w", pady=(8, 3))
        amount_entry = self.form_entry(form, amount_var, width=29)
        amount_entry.pack(fill="x", pady=(0, 2))

        def save():
            supplied_amount = amount_var.get().strip()
            if selected_status == "Completed":
                try:
                    shown_amount = money(float(supplied_amount or 0))
                except (TypeError, ValueError):
                    messagebox.showerror(
                        "Invalid Refund Amount", "Enter a valid refund amount.", parent=dialog)
                    return
                if shown_amount == money(0):
                    messagebox.showerror(
                        "Invalid Refund Amount", "The refund amount must be greater than zero.", parent=dialog)
                    return
                confirmed = messagebox.askyesno(
                    "Confirm Completed Refund",
                    f"Confirm that the actual refund for reservation {rid} has been sent?\n\n"
                    f"Refund amount: {shown_amount}\n\n"
                    "Only continue if the money has actually been transferred.",
                    parent=dialog)
                if not confirmed:
                    return
            ok, message = self.store.update_refund_status(
                refund_id, selected_status, self.current_user["username"],
                amount=supplied_amount)
            if not ok:
                messagebox.showerror("Refund Not Recorded", message, parent=dialog)
                return
            messagebox.showinfo("Refund Recorded", message, parent=dialog)
            dialog.destroy()
            self.show_admin_refunds()

        controls = tk.Frame(panel, bg=COLORS["card"])
        controls.pack(anchor="w", padx=22, pady=(14, 18))
        self.btn(controls, "SAVE REFUND", save, width=16).pack(side="left", padx=(0, 8))
        self.btn(controls, "CANCEL", dialog.destroy, primary=False, width=10).pack(side="left")

    def show_reservation_details(self, rid, customer_view=True):
        r = self.store.get_reservation(rid)
        if not r:
            messagebox.showerror("Not Found", "Reservation could not be found."); return
        if customer_view and r["username"] != self.current_user["username"]:
            messagebox.showerror("Access Denied", "You can only view your own bookings."); return
        if customer_view:
            self.clear()
            self.header("Reservation Details", f"Reservation ID: {rid}")
            page = self.page
        else:
            page = self.admin_shell("Reservation Details",
                                    f"Reservation ID: {rid}", active="manage")

        top_navigation = tk.Frame(page, bg=COLORS["bg"])
        top_navigation.pack(fill="x", padx=24, pady=(0, 4))
        if customer_view:
            self.btn(top_navigation, "BACK TO DASHBOARD", self.show_customer_dashboard,
                     primary=False).pack(side="left")
        else:
            self.btn(top_navigation, "BACK TO ADMIN", self.show_admin_dashboard,
                     primary=False).pack(side="left")

        details_row = tk.Frame(page, bg=COLORS["bg"])
        details_row.pack(fill="x", padx=22, pady=(0, 4))
        details_row.grid_columnconfigure(0, weight=1, uniform="details")
        details_row.grid_columnconfigure(1, weight=1, uniform="details")
        details_row.grid_rowconfigure(0, weight=1)

        left_card = self.card(details_row)
        left_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        right_card = self.card(details_row)
        right_card.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        payments = self.store.payments_for_reservation(rid)
        payment = payments[0] if payments else None
        pay_status, confirmed, pending, remaining = self.store.payment_status(rid)
        due = round(float(r["total"]) * DOWNPAYMENT_RATE, 2)
        if payment and payment.get("approval_status") == "Approved":
            approval_note = f"Approved by {payment.get('approved_by')} on {format_timestamp(payment.get('approved_at'))}"
        else:
            approval_note = "Not approved yet"
        self.label(left_card, "RESERVATION DETAILS", 13, COLORS["cyan"], True).pack(
            anchor="w", padx=20, pady=(12, 4))
        details = [
            ("Customer Name", r["customer"]), ("Reservation ID", r["reservation_id"]),
            ("Contact Number", r["contact"]), ("Vehicle Type", r["vehicle"]),
            ("Selected Service", r["service"]), ("Booking Date", r["booking_date"]),
            ("Booking Time", r["booking_time"]), ("Carwash Bay", r["bay"]),
            ("Booking Status", r["status"]),
        ]
        if r.get("status") == "Rejected":
            details.extend([
                ("Rejected By", r.get("rejected_by") or r.get("reviewed_by") or "N/A"),
                ("Rejection Date", format_timestamp(r.get("rejected_at") or r.get("reviewed_at"))),
                ("Rejection Reason", r.get("rejection_reason") or "Not recorded"),
            ])
        if r.get("status") == "Cancelled":
            details.extend([
                ("Cancelled By", r.get("cancelled_by") or "N/A"),
                ("Cancellation Date", format_timestamp(r.get("cancelled_at"))),
                ("Cancellation Reason", r.get("cancellation_reason") or "Not recorded"),
            ])
        status_colors_res = dict(RESERVATION_STATUS_COLORS)
        status_colors_res["Cancellation Requested"] = COLORS["yellow"]
        status_colors_res["Cancelled"] = COLORS["muted"]
        left_values, name_req = self.detail_rows(left_card, details, status_colors=status_colors_res)

        self.label(right_card, "PAYMENT DETAILS", 13, COLORS["cyan"], True).pack(
            anchor="w", padx=20, pady=(12, 4))
        payment_details = [
            ("Total Reservation Amount", money(r["total"])),
            ("Required Downpayment", money(due)),
            ("Amount Paid", money(confirmed)),
            ("Remaining Balance", money(remaining)),
            ("Payment Method", payment["method"] if payment else "N/A"),
            ("Payment Reference", payment["reference"] if payment else "N/A"),
            ("Payment Status", pay_status),
            ("Payment Approval", approval_note),
        ]
        if pending > 0:
            payment_details.insert(4, ("Awaiting Verification", money(pending)))
        status_colors = {"Fully Paid": COLORS["green"], "Partially Paid": COLORS["yellow"],
                         "Pending": COLORS["yellow"], "Unpaid": COLORS["muted"],
                         "Approved": COLORS["green"], "Completed": COLORS["green"],
                         "Cancelled": COLORS["red"], "Cancellation Requested": COLORS["yellow"]}
        right_values, payment_name_req = self.detail_rows(
            right_card, payment_details, status_colors=status_colors)
        eligibility = self.store.payment_eligibility(rid)
        # Remove down-payment warning per requirements
        downpayment_btn_frame = tk.Frame(right_card, bg=COLORS["card"])
        downpayment_btn_frame.pack(fill="x", padx=20, pady=(6, 12))
        wrap_state = {"limits": None}

        def resize_value_labels(*_):
            """Keep long values readable by wrapping them instead of clipping them."""
            page_width = page.winfo_width()
            if page_width <= 1:
                return
            card_share = (page_width - 44 - 12) / 2
            groups = ((left_values, name_req), (right_values, payment_name_req))
            limits = [max(50, int(card_share - 50 - width)) for _, width in groups]
            if limits == wrap_state["limits"]:
                return
            wrap_state["limits"] = list(limits)
            for (labels, _), limit in zip(groups, limits):
                for value_label in labels:
                    try:
                        value_label.configure(wraplength=limit)
                    except tk.TclError:
                        return

        details_row.bind("<Configure>", resize_value_labels)
        self.after_idle(resize_value_labels)

        # Show a database-backed refund request indicator only on the admin's
        # reservation details page and only when the customer actually submitted it.
        admin_refund = self.store.refund_for_reservation(rid) if not customer_view else None
        customer_requested_refund = bool(
            admin_refund and (
                admin_refund.get("requested_by_customer")
                or admin_refund.get("request_submitted_at")
                or admin_refund.get("customer_request")
            )
        )
        if customer_requested_refund:
            saved_refund_status = str(admin_refund.get("status") or "Pending").strip()
            status_labels = {
                "Pending": "REFUND REQUESTED BY CUSTOMER",
                "Under Review": "REFUND UNDER REVIEW",
                "Processing": "REFUND PROCESSING",
                "Completed": "REFUND COMPLETED",
                "Rejected": "REFUND REJECTED",
            }
            indicator_text = status_labels.get(
                saved_refund_status, "REFUND REQUESTED BY CUSTOMER"
            )
            indicator_color = self._refund_status_color(saved_refund_status)
            refund_indicator = tk.Frame(
                page, bg=COLORS["card"], highlightbackground=indicator_color,
                highlightthickness=1
            )
            refund_indicator.pack(fill="x", padx=22, pady=(0, 8))
            self.label(
                refund_indicator, f"↻  {indicator_text}", 10,
                indicator_color, True
            ).pack(anchor="w", padx=16, pady=9)

        history_card = self.card(page)
        history_card.pack(fill="x", padx=22, pady=(0, 8))
        self.label(history_card, "PAYMENT HISTORY", 13, COLORS["cyan"], True).pack(
            anchor="w", padx=20, pady=(8, 4))
        self.payment_history(history_card, rid, admin_view=not customer_view)

        actions = tk.Frame(page, bg=COLORS["bg"])
        actions.pack(fill="x", padx=22, pady=(4, 10))
        # Remove down-payment warning from UI if exists (already handled)
        if customer_view:
            if remaining > 0 and r["status"] not in ("Rejected", "Completed", "Cancelled", "Cancellation Requested"):
                if confirmed == 0 and due > 0:
                    pay_label = "SUBMIT DOWNPAYMENT"
                else:
                    pay_label = "PAY REMAINING BALANCE"
                submit_btn = self.btn(downpayment_btn_frame, pay_label, lambda: self.show_payment(rid),
                                      width=24)
                submit_btn.pack(anchor="w")
            self.btn(actions, "GENERATE RECEIPT", lambda: self.generate_receipt(rid),
                     primary=False, width=18).pack(side="left", padx=4, pady=4)
            self.btn(actions, "ADD ANOTHER RESERVATION", self.show_booking,
                     width=26).pack(side="left", padx=4, pady=4)
            if (r["status"] in ("Cancellation Requested", "Cancelled") and confirmed > 0.005):
                existing_refund = self.store.refund_for_reservation(rid)
                request_already_submitted = bool(
                    existing_refund and (
                        existing_refund.get("requested_by_customer")
                        or existing_refund.get("request_submitted_at")
                        or existing_refund.get("customer_request")
                    )
                )
                if request_already_submitted:
                    refund_status_text = f"REFUND REQUEST: {existing_refund.get('status') or 'Pending'}"
                    self.label(actions, refund_status_text, 9, self._refund_status_color(
                        existing_refund.get("status") or "Pending"), True).pack(
                            side="left", padx=8, pady=4)
                else:
                    self.btn(actions, "REQUEST REFUND",
                             lambda reservation_id=rid: self.customer_request_refund(reservation_id),
                             primary=True, width=17).pack(side="left", padx=4, pady=4)
            if r["status"] not in ("Completed", "Rejected", "Cancelled", "Cancellation Requested"):
                cancel_button = self.btn(
                    actions, "CANCEL RESERVATION",
                    lambda: self.confirm_cancel_reservation(rid), primary=False, width=22
                )
                cancel_button.configure(bg=COLORS["red"], activebackground="#c75a66")
                cancel_button.pack(side="left", padx=4, pady=4)
        else:
            self.btn(actions, "GENERATE RECEIPT", lambda: self.generate_receipt(rid), primary=False, width=18).pack(side="left", padx=4, pady=4)
            if customer_requested_refund and admin_refund:
                self.btn(
                    actions, "REVIEW REFUND REQUEST",
                    lambda refund_id=admin_refund.get("refund_id"), reservation_id=rid:
                        self.admin_update_refund(refund_id, reservation_id),
                    primary=True, width=24
                ).pack(side="left", padx=4, pady=4)
            if r["status"] == "Cancellation Requested":
                cancel_req_btn = self.btn(
                    actions, "CONFIRM CANCELLATION REQUEST",
                    lambda: self.admin_confirm_cancellation_request(rid), primary=True, width=40
                )
                cancel_req_btn.pack(side="left", padx=4, pady=4)

    def payment_history(self, parent, rid, admin_view=False):
        """Show every payment recorded for a reservation with per-payment admin actions."""
        payments = self.store.payments_for_reservation(rid)
        wrapper = tk.Frame(parent, bg=COLORS["card2"], highlightbackground="#23374e", highlightthickness=1)
        wrapper.pack(fill="x", padx=14, pady=(0, 10))
        canvas = tk.Canvas(wrapper, bg=COLORS["card2"], highlightthickness=0, borderwidth=0, height=60)
        hbar = ttk.Scrollbar(wrapper, orient="horizontal", command=canvas.xview)
        canvas.configure(xscrollcommand=hbar.set)
        canvas.pack(fill="x", expand=True)
        hbar.pack(fill="x")
        history = tk.Frame(canvas, bg=COLORS["card2"])
        window = canvas.create_window((0, 0), window=history, anchor="nw")

        def sync_scroll(*_):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfigure(window, height=history.winfo_reqheight())
            canvas.configure(height=max(40, history.winfo_reqheight()))

        history.bind("<Configure>", sync_scroll)
        headers = ("Payment ID", "Payment Date", "Method", "Amount Paid", "Reference",
                   "Status", "Verified By")
        column_widths = (12, 22, 12, 14, 20, 14, 16)
        for index, title in enumerate(headers):
            self.label(history, title, 9, COLORS["cyan"], True, width=column_widths[index], anchor="w",
                       bg=COLORS["card2"]).grid(row=0, column=index, sticky="ew", padx=6, pady=(8, 4))
        if not payments:
            self.label(history, "No payment has been recorded for this reservation yet.", 9,
                       COLORS["muted"], bg=COLORS["card2"]).grid(row=1, column=0, columnspan=len(headers),
                                                                 sticky="nsew", padx=6, pady=(4, 10))
            sync_scroll()
            return wrapper
        for index, record in enumerate(payments, start=1):
            verification = record.get("verification_status") or "Unverified"
            approved = record.get("approval_status") == "Approved"
            status = "Approved" if approved else verification
            values = (
                f"PAY-{int(record['payment_id']):03d}",
                format_timestamp(record.get("submitted_at")),
                record.get("method") or "N/A",
                money(record.get("amount_submitted") or 0),
                record.get("reference") or "N/A",
                status,
                record.get("verified_by") or "Not yet verified",
            )
            data_row = index * 2 - 1
            for column, value in enumerate(values):
                color = "white"
                if column == 5:
                    color = {"Approved": COLORS["green"], "Verified": COLORS["green"],
                             "Rejected": COLORS["red"], "Unverified": COLORS["yellow"]}.get(status, "white")
                self.label(history, value, 9, color, True, width=column_widths[column], anchor="w",
                           bg=COLORS["card2"]).grid(row=data_row, column=column, sticky="ew", padx=6, pady=2)
            image_path = self.store.payment_image_path(record)
            if admin_view or image_path:
                row_actions = tk.Frame(history, bg=COLORS["card2"])
                row_actions.grid(row=data_row + 1, column=0, columnspan=len(headers),
                                 sticky="e", padx=6, pady=(0, 6))
                if image_path:
                    self.btn(row_actions, "VIEW IMAGE", primary=False, width=11,
                             command=lambda p=record: self.show_payment_image(p)).pack(side="left", padx=2)
                if admin_view:
                    if verification != "Verified":
                        self.btn(row_actions, "VERIFY", primary=True, width=9,
                                 command=lambda pid=record["payment_id"]: self.admin_verify_payment(pid, "Verified", rid)).pack(side="left", padx=2)
                    if verification == "Verified" and not approved:
                        self.btn(row_actions, "APPROVE", primary=True, width=9,
                                 command=lambda pid=record["payment_id"]: self.admin_approve_payment(pid, rid)).pack(side="left", padx=2)
                    if verification == "Verified":
                        self.btn(row_actions, "UNVERIFY", primary=False, width=9,
                                 command=lambda pid=record["payment_id"]: self.admin_verify_payment(pid, "Unverified", rid)).pack(side="left", padx=2)
                    if verification != "Rejected" and verification != "Verified" and not approved:
                        self.btn(row_actions, "REJECT", primary=False, width=9,
                                 command=lambda pid=record["payment_id"]: self.admin_verify_payment(pid, "Rejected", rid)).pack(side="left", padx=2)
        sync_scroll()
        return wrapper

    def customer_request_refund(self, rid):
        """Open the refund details form directly from the customer's refund button."""
        self.show_refund_details_form(rid)

    def confirm_cancel_reservation(self, rid):
        """Ask for explicit confirmation before cancelling this reservation."""
        r = self.store.get_reservation(rid)
        if not r:
            messagebox.showerror("Cancellation Failed", "This reservation could not be found.")
            return
        if self.current_user and self.current_user.get("role") == "customer" and r["username"] != self.current_user["username"]:
            messagebox.showerror("Access Denied", "You can only cancel your own reservations.")
            return
        if r["status"] in ("Completed", "Rejected", "Cancelled", "Cancellation Requested"):
            messagebox.showwarning(
                "Cannot Cancel Reservation",
                f"This reservation cannot be cancelled because its status is {r['status']}."
            )
            return

        dialog = tk.Toplevel(self)
        dialog.title("Confirm Reservation Cancellation")
        dialog.configure(bg=COLORS["card"])
        dialog.transient(self)
        dialog.resizable(False, False)
        dialog.grab_set()
        cancellation_message = "Are you sure you want to cancel this reservation?"
        if r.get("status") == "Approved" and self.store._verified_total(rid) > 0.005:
            fee = cancellation_fee(r.get("total") or 0)
            cancellation_message += (
                f"\n\nIMPORTANT: Because this reservation is approved and has a verified payment, "
                f"{money(fee)} (30% of the total booking price) will be non-refundable. "
                "Any refund will be calculated from the actual verified amount paid."
            )
        self.label(dialog, cancellation_message, 11,
                   COLORS["white"], True, wraplength=420, justify="center").pack(padx=24, pady=(22, 16))
        choices = tk.Frame(dialog, bg=COLORS["card"])
        choices.pack(padx=16, pady=(0, 20))

        def keep_reservation():
            dialog.destroy()

        def cancel_reservation():
            try:
                latest = self.store.get_reservation(rid)
                if not latest or (self.current_user and self.current_user.get("role") == "customer" and latest["username"] != self.current_user["username"]):
                    messagebox.showerror("Cancellation Failed", "The reservation could not be verified.", parent=dialog)
                    return
                if latest["status"] in ("Completed", "Rejected", "Cancelled", "Cancellation Requested"):
                    messagebox.showwarning("Cannot Cancel Reservation", "This reservation's status has changed and it can no longer be cancelled.", parent=dialog)
                    dialog.destroy()
                    self.show_reservation_details(rid, customer_view=(self.current_user.get("role") == "customer" if self.current_user else True))
                    return
                actor = self.current_user["username"] if self.current_user else "system"
                actor_role = self.current_user.get("role") if self.current_user else None
                is_customer = actor_role == "customer"
                self.store.set_booking_status(rid, "Cancelled", actor, actor_role=actor_role, customer_request=is_customer)
            except Exception as exc:
                error_msg = str(exc)
                if "Only active staff" in error_msg or "permission" in error_msg.lower():
                    messagebox.showerror("Access Denied", error_msg, parent=dialog)
                else:
                    messagebox.showerror("Cancellation Failed", f"The reservation could not be cancelled.\n\n{error_msg}", parent=dialog)
                return
            dialog.destroy()
            latest = self.store.get_reservation(rid)
            if latest and latest.get("status") == "Cancellation Requested":
                messagebox.showinfo("Cancellation Request Submitted", f"A cancellation request has been submitted for Reservation {rid}. An administrator will review it.")
            elif latest and latest.get("status") == "Cancelled":
                messagebox.showinfo("Reservation Cancelled", f"Reservation {rid} has been cancelled.")
            else:
                messagebox.showinfo("Update Complete", f"Reservation {rid} update processed.")
            self.show_reservation_details(rid, customer_view=(self.current_user.get("role") == "customer" if self.current_user else True))

        self.btn(choices, "YES, CANCEL RESERVATION", cancel_reservation, width=24).pack(side="left", padx=5)
        self.btn(choices, "NO, GO BACK", keep_reservation, primary=False, width=22).pack(side="left", padx=5)
        dialog.update_idletasks()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - dialog.winfo_width()) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - dialog.winfo_height()) // 2)
        dialog.geometry(f"+{x}+{y}")
        dialog.protocol("WM_DELETE_WINDOW", keep_reservation)

    def show_payment(self, rid):
        r = self.store.get_reservation(rid)
        if not r or r["username"] != self.current_user["username"]:
            messagebox.showerror("Access Denied", "Reservation not found."); return
        eligibility = self.store.payment_eligibility(rid)
        if not eligibility or eligibility["outstanding"] <= 0.005:
            messagebox.showinfo("Nothing Due", "This reservation is already fully paid.")
            self.show_reservation_details(rid, customer_view=True); return
        total = eligibility["total"]
        due = eligibility["downpayment"]
        confirmed = eligibility["confirmed"]
        pending = eligibility["pending"]
        remaining = eligibility["outstanding"]
        available = eligibility["outstanding"]
        downpayment_available = eligibility["downpayment_available"]
        full_available = eligibility["full_available"]
        default_option = (PAYMENT_OPTION_DOWNPAYMENT if downpayment_available
                          else PAYMENT_OPTION_FULL)
        downpayment_label = (PAYMENT_OPTION_DOWNPAYMENT if downpayment_available
                             else f"{PAYMENT_OPTION_DOWNPAYMENT} — Unavailable")
        full_label = (PAYMENT_OPTION_FULL if full_available
                      else f"{PAYMENT_OPTION_FULL} — Unavailable")
        self.clear()
        self.header("Payment", "Pay the 30% downpayment or settle the full balance.")
        panel = self.card(self.page); panel.pack(fill="both", expand=True, padx=150, pady=12)
        self.label(panel, f"Reservation: {rid}", 13, COLORS["cyan"], True).pack(pady=(16, 4))
        self.label(panel, f"Total: {money(total)}     Required downpayment (30%): {money(due)}", 10, bold=True).pack(pady=3)
        self.label(panel, f"Amount paid so far (confirmed): {money(confirmed)}", 10, COLORS["cyan"], True).pack(pady=3)
        self.label(panel, "Payment option", 10, COLORS["cyan"], True).pack(pady=(10, 3))
        option = tk.StringVar(value=default_option)
        option_box = tk.Frame(panel, bg=COLORS["card"])
        option_box.pack(pady=(2, 2))
        option_box_inner = tk.Frame(option_box, bg=COLORS["card"])
        option_box_inner.pack()

        def option_radio(text, value, enabled, parent=None):
            rb = tk.Radiobutton(
                parent or option_box_inner, text=text, variable=option, value=value,
                command=lambda: apply_option(),
                state="normal" if enabled else "disabled",
                font=("Segoe UI", 10, "bold"),
                fg=COLORS["white"] if enabled else COLORS["muted"],
                bg=COLORS["card"], activebackground=COLORS["card"],
                activeforeground=COLORS["cyan"], selectcolor=COLORS["card2"],
                highlightthickness=0, bd=0,
                cursor="hand2" if enabled else "arrow"
            )
            return rb

        # Keep the policy notes immediately to the right of each payment option.
        downpayment_row = tk.Frame(option_box_inner, bg=COLORS["card"])
        downpayment_row.pack(anchor="center", pady=(0, 2))
        option_radio(downpayment_label, PAYMENT_OPTION_DOWNPAYMENT,
                     downpayment_available, downpayment_row).pack(side="left")

        downpayment_error = self.downpayment_error_label(option_box, eligibility, wraplength=460)
        if downpayment_error is not None:
            downpayment_error.pack(anchor="center", padx=20, pady=(0, 4))

        fullpayment_row = tk.Frame(option_box_inner, bg=COLORS["card"])
        fullpayment_row.pack(anchor="center", pady=(0, 2))
        option_radio(full_label, PAYMENT_OPTION_FULL,
                     full_available, fullpayment_row).pack(side="left")
        if not full_available:
            self.label(option_box, eligibility["full_message"], 9, COLORS["yellow"], True,
                       wraplength=460, justify="center").pack(anchor="center", padx=20, pady=(0, 4))
        self.label(panel, "Choose your payment method", 10, COLORS["cyan"], True).pack(pady=(10, 3))
        method = tk.StringVar(value="")
        method_box = ttk.Combobox(panel, textvariable=method, values=PAYMENT_METHODS,
                                  state="readonly", width=25)
        method_box.pack()
        policy_state = {"accepted_method": None, "dialog_open": False}

        def confirm_payment_policy(event=None):
            selected_method = method.get().strip()
            if not selected_method or policy_state["dialog_open"]:
                return
            if policy_state["accepted_method"] == selected_method:
                return

            policy_state["dialog_open"] = True
            dialog = tk.Toplevel(self)
            dialog.title("Payment and Cancellation Policy")
            dialog.transient(self)
            dialog.resizable(False, False)
            dialog.configure(bg=COLORS["card"])
            dialog.grab_set()

            tk.Label(
                dialog, text="PAYMENT AND CANCELLATION POLICY",
                bg=COLORS["card"], fg=COLORS["yellow"],
                font=("Segoe UI", 12, "bold")
            ).pack(padx=24, pady=(20, 10))
            tk.Label(
                dialog,
                text=(f"Selected payment method: {selected_method}\n\n"
                      "If your reservation has been approved by the admin and you later cancel it, "
                      "30% of the total booking price is non-refundable. The remaining refundable "
                      "amount, if any, is based on the actual amount paid.\n\n"
                      "This policy applies to both GCash and Bank Transfer, including down payments "
                      "and full payments. Do you agree?"),
                bg=COLORS["card"], fg=COLORS["white"],
                font=("Segoe UI", 10), justify="left", wraplength=470
            ).pack(padx=24, pady=(0, 16))

            actions = tk.Frame(dialog, bg=COLORS["card"])
            actions.pack(pady=(0, 18))

            def agree_and_continue():
                policy_state["accepted_method"] = selected_method
                policy_state["dialog_open"] = False
                dialog.destroy()

            def cancel_policy():
                policy_state["accepted_method"] = None
                policy_state["dialog_open"] = False
                dialog.destroy()
                method.set("")
                method_box.focus_set()

            self.btn(actions, "AGREE & CONTINUE", agree_and_continue, width=20).pack(side="left", padx=6)
            self.btn(actions, "CANCEL", cancel_policy, primary=False, width=12).pack(side="left", padx=6)
            dialog.protocol("WM_DELETE_WINDOW", cancel_policy)
            dialog.update_idletasks()
            x = self.winfo_rootx() + max(0, (self.winfo_width() - dialog.winfo_width()) // 2)
            y = self.winfo_rooty() + max(0, (self.winfo_height() - dialog.winfo_height()) // 2)
            dialog.geometry(f"+{x}+{y}")
            dialog.wait_window()
            policy_state["dialog_open"] = False

        method_box.bind("<<ComboboxSelected>>", confirm_payment_policy)
        qr_label = tk.Label(
            panel,
            image="",
            text="Select a payment method to display the QR code.",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=("Segoe UI", 9),
            justify="center"
        )
        qr_label.pack(pady=10)
        payload_label = self.label(panel, "Payload: (select a payment method)", 9, COLORS["muted"])
        payload_label.pack(pady=3)

        def update_qr(*_):
            selected = method.get()
            payload_label.config(text=f"Payload: WASHAPP|{selected}|{rid}|{due:.2f}" if selected
                                 else "Payload: (select a payment method)")
            photo = load_payment_qr(selected) if selected else None
            if photo is None:
                qr_label.config(image="", text="Select a payment method to display the QR code."
                                if not selected else "Could not load the payment QR image.")
                qr_label.image = None
            else:
                qr_label.config(image=photo, text="")
                qr_label.image = photo
        method.trace_add("write", update_qr)
        update_qr()
        self.label(panel, "Payment reference image", 10, COLORS["cyan"], True).pack(pady=(8, 3))
        selected_image = {"path": None, "photo": None}
        upload_row = tk.Frame(panel, bg=COLORS["card"])
        upload_row.pack(pady=(0, 4))
        upload_button = self.btn(upload_row, "UPLOAD PAYMENT REFERENCE IMAGE", lambda: choose_image(), width=29)
        upload_button.pack(side="left", padx=3)
        change_button = self.btn(upload_row, "CHANGE IMAGE", lambda: choose_image(), primary=False, width=15)
        preview_holder = tk.Frame(panel, bg=COLORS["card2"], highlightbackground="#23374e", highlightthickness=1)
        preview_holder.pack(padx=10, pady=6)
        image_preview = tk.Label(
            preview_holder, text="No payment reference image selected.", bg=COLORS["card2"],
            fg=COLORS["muted"], font=("Segoe UI", 9), width=44, height=8
        )
        image_preview.pack(padx=6, pady=6)
        image_info = self.label(
            panel, f"Accepted formats: PNG, JPG, JPEG  •  Maximum size: {format_file_size(MAX_PAYMENT_IMAGE_BYTES)}",
            9, COLORS["muted"]
        )
        image_info.pack(pady=(0, 2))

        def choose_image():
            chosen = filedialog.askopenfilename(
                title="Select Payment Reference Image",
                filetypes=[("Image files", "*.png *.jpg *.jpeg"), ("PNG images", "*.png"),
                           ("JPEG images", "*.jpg *.jpeg"), ("All files", "*.*")]
            )
            if not chosen:
                return
            ok, message = validate_payment_image(chosen)
            if not ok:
                messagebox.showerror("Invalid Payment Image", message)
                return
            photo = load_payment_image(chosen, max_width=320, max_height=190)
            if photo is None:
                messagebox.showerror("Invalid Payment Image",
                                     "This file could not be displayed as an image. "
                                     "Please choose a valid PNG, JPG, or JPEG file.")
                return
            selected_image["path"] = chosen
            selected_image["photo"] = photo
            image_preview.config(image=photo, text="")
            image_preview.image = photo
            image_info.config(
                text=f"Selected: {Path(chosen).name} ({format_file_size(Path(chosen).stat().st_size)})  "
                     f"•  Click CHANGE IMAGE to replace it."
            )
            upload_button.pack_forget()
            change_button.pack(side="left", padx=3)

        downpayment_remaining = max(0.0, round(float(due) - float(confirmed), 2))
        if downpayment_remaining < 0:
            downpayment_remaining = 0.0
        initial_amount = round(downpayment_remaining if default_option == PAYMENT_OPTION_DOWNPAYMENT else available, 2)
        amount_var = tk.StringVar(value=f"{max(0.0, initial_amount):.2f}")
        amount_display = tk.StringVar(value=money(initial_amount))
        self.label(panel, "Amount submitted (PHP)", 10, COLORS["cyan"], True).pack(pady=(8, 3))
        amount_entry = tk.Entry(panel, textvariable=amount_display, font=("Segoe UI", 11), relief="flat", width=20, state="readonly", readonlybackground=COLORS["card2"], fg=COLORS["white"], justify="center")
        amount_entry.pack(ipady=7, anchor="center")
        projection = self.label(panel, "", 9, COLORS["muted"])
        projection.pack(pady=(6, 0))

        def apply_option(*_):
            target = downpayment_remaining if option.get() == PAYMENT_OPTION_DOWNPAYMENT else available
            amount_var.set(f"{round(max(0.0, float(target)), 2):.2f}")

        def update_amount_display(*_):
            try:
                amount_display.set(money(float(amount_var.get())))
            except (TypeError, ValueError):
                amount_display.set(amount_var.get())

        def refresh_projection(*_):
            try:
                value = round(float(amount_var.get()), 2)
            except ValueError:
                value = 0.0
            value = max(0.0, min(value, remaining))
            projected_remaining = round(max(0.0, remaining - value), 2)
            projected_status = payment_status_label(total, round(confirmed + value, 2), pending)
            projection.config(text=f"Remaining Balance: {money(projected_remaining)}     Payment Status: {projected_status}")

        amount_var.trace_add("write", update_amount_display)
        amount_var.trace_add("write", refresh_projection)
        refresh_projection()

        submission = {"busy": False}

        def submit_payment():
            if submission["busy"]:
                return
            submission["busy"] = True
            try:
                current = self.store.payment_eligibility(rid)
                if not current or current["outstanding"] <= 0.005:
                    messagebox.showinfo("Nothing Due", "This reservation is already fully paid.")
                    self.show_reservation_details(rid, customer_view=True)
                    return
                selected_option = option.get()
                if (selected_option.startswith(PAYMENT_OPTION_DOWNPAYMENT)
                        and not current["downpayment_available"]):
                    messagebox.showwarning("30% Payment Unavailable",
                                           current["downpayment_message"])
                    return
                if (selected_option.startswith(PAYMENT_OPTION_FULL)
                        and not current["full_available"]):
                    messagebox.showwarning("Full Payment Unavailable",
                                           current["full_message"])
                    return
                if not method.get():
                    messagebox.showwarning("Select Payment Method", "Please select GCash or Bank Transfer before submitting your payment.")
                    return
                try:
                    amount = float(amount_var.get())
                except ValueError:
                    messagebox.showerror("Invalid Amount", "Enter a valid numeric amount.")
                    return
                if selected_option.startswith(PAYMENT_OPTION_DOWNPAYMENT):
                    expected = round(max(0.0, float(current["downpayment"]) - float(current["confirmed"])), 2)
                    if abs(amount - expected) > 0.005:
                        messagebox.showerror("Invalid Amount",
                                             f"The amount for Downpayment (30%) must be {money(expected)}.")
                        return
                    if amount > expected + 0.005 or amount > current["outstanding"] + 0.005:
                        messagebox.showerror("Invalid Amount",
                                             "The submitted amount exceeds the required amount for the selected payment option.")
                        return
                elif selected_option.startswith(PAYMENT_OPTION_FULL):
                    expected = round(float(current["outstanding"]), 2)
                    if abs(amount - expected) > 0.005 or amount > expected + 0.005:
                        messagebox.showerror("Invalid Amount",
                                             f"The amount for Full Payment must be {money(expected)}.")
                        return
                if amount <= 0:
                    messagebox.showerror("Invalid Amount", "Amount must be greater than zero.")
                    return
                if amount > current["outstanding"] + 0.005:
                    messagebox.showerror(
                        "Invalid Amount",
                        f"You can pay up to {money(current['outstanding'])} for this reservation. "
                        f"The remaining balance is {money(current['outstanding'])}."
                    )
                    return
                if (selected_option.startswith(PAYMENT_OPTION_DOWNPAYMENT)
                        and amount > current["downpayment_remaining"] + 0.005):
                    messagebox.showerror(
                        "Invalid Amount",
                        f"The 30% down payment option can only cover the remaining "
                        f"down payment of {money(current['downpayment_remaining'])}."
                    )
                    return
                selected_method = method.get().strip()
                if policy_state["accepted_method"] != selected_method:
                    messagebox.showwarning(
                        "Policy Confirmation Required",
                        "Please select your payment method and click AGREE & CONTINUE "
                        "on the payment policy confirmation before submitting.",
                        parent=self
                    )
                    return
                try:
                    self.store.add_payment(
                        rid, selected_method, current["downpayment"], amount,
                        reference_image=selected_image["path"],
                        payment_option=selected_option,
                        policy_accepted=True
                    )
                except ValueError as exc:
                    messagebox.showerror("Payment Not Recorded", str(exc))
                    return
                messagebox.showinfo("Payment Submitted",
                                    "Your payment was recorded as PENDING.\n"
                                    "It becomes successful only after the admin verifies the transaction.\n"
                                    f"Remaining balance after verification: "
                                    f"{money(max(0.0, current['outstanding'] - amount))}")
                self.show_reservation_details(rid, customer_view=True)
            finally:
                submission["busy"] = False
        buttons = tk.Frame(panel, bg=COLORS["card"]); buttons.pack(pady=16)
        self.btn(buttons, "SUBMIT PAYMENT", submit_payment, width=22).pack(side="left", padx=5)
        self.btn(buttons, "CANCEL", lambda: self.show_reservation_details(rid, True), primary=False).pack(side="left", padx=5)

    def show_admin_dashboard(self):
        if not self.is_staff_or_admin():
            messagebox.showerror("Access Denied", "Staff or admin access is required.")
            self.show_landing()
            return
        self.store.sync_admin_notifications()
        content = self.admin_shell(
            "Admin Dashboard",
            "Operational overview of bookings, collections, and work awaiting action.",
            active="dashboard",
        )
        counts = self.store.counts()
        approved_value, completed_value, _paid = self.store.sales_summary()
        collection = self.store.payment_summary()
        pending_refunds = sum(1 for refund in self.store.data["refunds"]
                              if refund.get("status") in ("Pending", "Processing"))
        unread = self.store.unread_notification_count(self.current_user["username"])

        hero = self.card(content)
        hero.pack(fill="x", pady=(0, 10))
        hero_body = tk.Frame(hero, bg=COLORS["card"])
        hero_body.pack(fill="x", padx=16, pady=12)
        self.label(hero_body, f"{counts['pending']} booking(s) awaiting approval",
                   13, COLORS["white"], True).pack(anchor="w")
        self.label(hero_body,
                   (f"{collection['pending_count']} payment(s) awaiting verification • "
                    f"{pending_refunds} refund(s) in progress • {unread} unread alert(s)"),
                   9, COLORS["muted"], wraplength=380, justify="left").pack(
                       anchor="w", pady=(2, 8))
        quick = tk.Frame(hero_body, bg=COLORS["card"])
        quick.pack(fill="x")
        self.btn(quick, "MANAGE BOOKINGS", self.show_admin_manage_bookings).pack(
            side="left", padx=(0, 6))
        self.btn(quick, "VERIFY PAYMENTS", self.show_admin_payment_verification,
                 primary=False).pack(side="left", padx=(0, 6))
        if self.is_admin_user():
            self.btn(quick, "VIEW REPORTS", self.show_admin_reports,
                     primary=False).pack(side="left")

        self.label(content, "BOOKING SUMMARY", 12, COLORS["cyan"], True,
                   bg=COLORS["bg"]).pack(anchor="w")
        summary = tk.Frame(content, bg=COLORS["bg"])
        summary.pack(fill="x", pady=(4, 8))
        metrics = [
            ("Total Bookings", counts["total"], "▦", COLORS["cyan"]),
            ("Pending", counts["pending"], "◔", "#ffd34d"),
            ("Approved", counts["approved"], "✓", "#36d98a"),
            ("Rejected", counts["rejected"], "✕", "#ff6b6b"),
            ("Completed", counts["completed"], "★", "#8ce0ff"),
            ("Approved Booking Value", money(approved_value), "₱", COLORS["cyan"]),
            ("Completed Sales", money(completed_value), "◈", "#8fb8ff"),
            ("Total Collected", money(collection["total_collected"]), "∑", "#36d98a"),
        ]
        summary_cards = [
            self.admin_metric_card(summary, name, value, icon, accent)
            for name, value, icon, accent in metrics
        ]
        summary_layout = {"columns": None}

        def layout_summary_cards(event=None):
            width = event.width if event is not None else summary.winfo_width()
            columns = 4 if width >= 800 else 2
            if summary_layout["columns"] == columns:
                return
            summary_layout["columns"] = columns
            for column in range(4):
                summary.columnconfigure(column, weight=0)
            for column in range(columns):
                summary.columnconfigure(column, weight=1,
                                        uniform=f"dashboard_summary_{columns}")
            for index, cell in enumerate(summary_cards):
                cell.grid(row=index // columns, column=index % columns,
                          sticky="nsew", padx=4, pady=4)

        summary.bind("<Configure>", layout_summary_cards)
        layout_summary_cards()

        collection_card = self.card(content)
        collection_card.pack(fill="x", pady=(0, 10))
        collection_heading = tk.Frame(collection_card, bg=COLORS["card"])
        collection_heading.pack(fill="x", padx=16, pady=(10, 4))
        self.label(collection_heading, "PAYMENT SUMMARY", 12, COLORS["cyan"], True).pack(
            side="left")
        self.label(collection_heading, "Verified collections and balances.",
                   8, COLORS["muted"]).pack(side="right")
        collection_grid = tk.Frame(collection_card, bg=COLORS["card"])
        collection_grid.pack(fill="x", padx=8, pady=(0, 4))
        collection_metrics = [
            ("Total Amount Collected", money(collection["total_collected"]), COLORS["cyan"]),
            ("Verified Payments", f"{collection['verified_count']} verified", "#36d98a"),
            ("Awaiting Verification",
             f"{money(collection['pending_amount'])} ({collection['pending_count']})",
             "#ffd34d"),
            ("Outstanding Balance", money(collection["outstanding"]), "#ff8a95"),
            ("GCash Collected",
             money(collection["by_method"].get("GCash", {}).get("verified", 0)),
             COLORS["cyan"]),
            ("Bank Transfer Collected",
             money(collection["by_method"].get("Bank Transfer", {}).get("verified", 0)),
             "#8fb8ff"),
        ]
        collection_cells = [
            self.admin_payment_metric(collection_grid, name, value, color)
            for name, value, color in collection_metrics
        ]
        collection_layout = {"columns": None}

        def layout_collection_cards(event=None):
            width = event.width if event is not None else collection_grid.winfo_width()
            columns = 3 if width >= 680 else 2
            if collection_layout["columns"] == columns:
                return
            collection_layout["columns"] = columns
            for column in range(3):
                collection_grid.columnconfigure(column, weight=0)
            for column in range(columns):
                collection_grid.columnconfigure(column, weight=1,
                                                uniform=f"dashboard_payments_{columns}")
            for index, cell in enumerate(collection_cells):
                cell.grid(row=index // columns, column=index % columns,
                          sticky="nsew", padx=4, pady=4)

        collection_grid.bind("<Configure>", layout_collection_cards)
        layout_collection_cards()

        recent_card = self.card(content)
        recent_card.pack(fill="x", pady=(0, 10))
        recent_head = tk.Frame(recent_card, bg=COLORS["card"])
        recent_head.pack(fill="x", padx=16, pady=(10, 6))
        self.label(recent_head, "RECENT BOOKINGS", 12, COLORS["cyan"], True).pack(side="left")
        self.btn(recent_head, "VIEW ALL", self.show_admin_manage_bookings,
                 primary=False).pack(side="right")
        recent = self.store.all_reservations()[:6]
        self.configure_admin_table_style()
        recent_tree = ttk.Treeview(
            recent_card, columns=("id", "customer", "service", "date", "time",
                                  "total", "status", "payment"),
            show="headings", style="Admin.Treeview", selectmode="browse", height=6,
        )
        recent_names = {
            "id": "Reservation", "customer": "Customer", "service": "Service",
            "date": "Date", "time": "Time", "total": "Total", "status": "Booking",
            "payment": "Payment",
        }
        for column, heading in recent_names.items():
            recent_tree.heading(column, text=heading)
            recent_tree.column(column, width=100 if column != "id" else 105,
                               anchor="center")
        recent_tree.tag_configure("pending", foreground=RESERVATION_STATUS_COLORS["Pending"])
        recent_tree.tag_configure("approved", foreground=RESERVATION_STATUS_COLORS["Approved"])
        recent_tree.tag_configure("rejected", foreground=RESERVATION_STATUS_COLORS["Rejected"])
        recent_tree.tag_configure("completed", foreground=RESERVATION_STATUS_COLORS["Completed"])
        recent_tree.tag_configure("cancelled", foreground=COLORS["muted"])
        recent_tree.tag_configure("odd", background="#0b1624")
        recent_tree.tag_configure("even", background="#101d2d")
        for index, row in enumerate(recent):
            recent_tree.insert("", "end", iid=row["reservation_id"], values=(
                row["reservation_id"], row["customer"], row["service"],
                row["booking_date"], row["booking_time"], money(row["total"]),
                row["status"], row["payment_status"],
            ), tags=(str(row["status"]).lower(), "odd" if index % 2 == 0 else "even"))
        recent_tree.bind("<Double-1>", lambda _event: self._open_recent_booking(recent_tree))
        recent_tree.pack(fill="x", padx=10, pady=(0, 10))
        self._admin_recent_tree = recent_tree
        if not recent:
            self.label(recent_card, "No bookings have been recorded yet.", 9,
                       COLORS["muted"], bg=COLORS["card"]).pack(padx=16, pady=(0, 12))

    def _open_recent_booking(self, tree):
        selection = tree.selection()
        if selection:
            self.show_reservation_details(selection[0], customer_view=False)

    def show_admin_manage_bookings(self):
        if not self.is_staff_or_admin():
            messagebox.showerror("Access Denied", "Staff or admin access is required.")
            self.show_landing()
            return
        filter_value = self.admin_filter.get() if hasattr(self, "admin_filter") else "All"
        search_value = self.admin_search.get() if hasattr(self, "admin_search") else ""
        content = self.admin_shell(
            "Manage Bookings",
            "Review booking approvals, payment status, and reservation details.",
            active="manage",
        )
        counts = self.store.counts()
        approved_value, completed_value, paid = self.store.sales_summary()
        collection = self.store.payment_summary()

        overview = tk.Frame(content, bg=COLORS["bg"])
        overview.pack(fill="x", pady=(0, 4))
        self.label(overview, "BOOKING SUMMARY", 12, COLORS["cyan"], True,
                   bg=COLORS["bg"]).pack(side="left")
        self.label(overview, "Live totals from saved reservations.", 8,
                   COLORS["muted"], bg=COLORS["bg"]).pack(side="right")

        summary = tk.Frame(content, bg=COLORS["bg"])
        summary.pack(fill="x", pady=(0, 8))
        metrics = [
            ("Total Bookings", counts["total"], "▦", COLORS["cyan"]),
            ("Pending", counts["pending"], "◔", "#ffd34d"),
            ("Approved", counts["approved"], "✓", "#36d98a"),
            ("Rejected", counts["rejected"], "✕", "#ff6b6b"),
            ("Completed", counts["completed"], "★", "#8ce0ff"),
            ("Approved Booking Value", money(approved_value), "₱", COLORS["cyan"]),
            ("Completed Sales", money(completed_value), "◈", "#8fb8ff"),
            ("Total Collected", money(collection["total_collected"]), "∑", "#36d98a"),
        ]
        summary_cards = [
            self.admin_metric_card(summary, name, value, icon, accent)
            for name, value, icon, accent in metrics
        ]
        summary_layout = {"columns": None}

        def layout_summary_cards(event=None):
            width = event.width if event is not None else summary.winfo_width()
            columns = 4 if width >= 900 else 2
            if summary_layout["columns"] == columns:
                return
            summary_layout["columns"] = columns
            for column in range(4):
                summary.columnconfigure(column, weight=0)
            for column in range(columns):
                summary.columnconfigure(column, weight=1,
                                        uniform=f"booking_summary_{columns}")
            for index, card in enumerate(summary_cards):
                card.grid(row=index // columns, column=index % columns,
                          sticky="nsew", padx=5, pady=5)

        summary.bind("<Configure>", layout_summary_cards)
        layout_summary_cards()

        collection_card = self.card(content)
        collection_card.pack(fill="x", pady=(0, 10))
        collection_heading = tk.Frame(collection_card, bg=COLORS["card"])
        collection_heading.pack(fill="x", padx=16, pady=(10, 4))
        self.label(collection_heading, "PAYMENT SUMMARY", 12, COLORS["cyan"], True).pack(
            side="left")
        self.label(collection_heading, "Collections and balances.",
                   8, COLORS["muted"]).pack(side="right")
        collection_grid = tk.Frame(collection_card, bg=COLORS["card"])
        collection_grid.pack(fill="x", padx=8, pady=(0, 4))
        collection_metrics = [
            ("Total Amount Collected", money(collection["total_collected"]), COLORS["cyan"]),
            ("Verified Payments", f"{collection['verified_count']} verified",
             "#36d98a"),
            ("Awaiting Verification",
             f"{money(collection['pending_amount'])} ({collection['pending_count']})",
             "#ffd34d"),
            ("Outstanding Balance", money(collection["outstanding"]), "#ff8a95"),
            ("GCash Collected",
             money(collection["by_method"].get("GCash", {}).get("verified", 0)),
             COLORS["cyan"]),
            ("Bank Transfer Collected",
             money(collection["by_method"].get("Bank Transfer", {}).get("verified", 0)),
             "#8fb8ff"),
        ]
        collection_cells = [
            self.admin_payment_metric(collection_grid, name, value, color)
            for name, value, color in collection_metrics
        ]
        collection_layout = {"columns": None}

        def layout_collection_cards(event=None):
            width = event.width if event is not None else collection_grid.winfo_width()
            columns = 3 if width >= 760 else 2
            if collection_layout["columns"] == columns:
                return
            collection_layout["columns"] = columns
            for column in range(3):
                collection_grid.columnconfigure(column, weight=0)
            for column in range(columns):
                collection_grid.columnconfigure(column, weight=1,
                                                uniform=f"payment_summary_{columns}")
            for index, cell in enumerate(collection_cells):
                cell.grid(row=index // columns, column=index % columns,
                          sticky="nsew", padx=5, pady=5)

        collection_grid.bind("<Configure>", layout_collection_cards)
        layout_collection_cards()

        section = tk.Frame(content, bg=COLORS["bg"])
        section.pack(fill="x", pady=(0, 5))
        self.label(section, "RESERVATIONS", 12, COLORS["cyan"], True,
                   bg=COLORS["bg"]).pack(side="left")
        self.admin_table_count = self.label(section, "Showing 0 reservations", 8,
                                            COLORS["muted"], bg=COLORS["bg"])
        self.admin_table_count.pack(side="right")

        # Compact, responsive reservation-status legend. The central palette above
        # is shared with status styling elsewhere in the application.
        legend_card = self.card(content)
        legend_card.pack(fill="x", pady=(0, 8))
        legend_inner = tk.Frame(legend_card, bg=COLORS["card"])
        legend_inner.pack(fill="x", padx=12, pady=8)
        self.label(legend_inner, "BOOKING STATUS GUIDE", 8,
                   COLORS["muted"], True, bg=COLORS["card"]).pack(anchor="w", pady=(0, 5))
        legend_items_frame = tk.Frame(legend_inner, bg=COLORS["card"])
        legend_items_frame.pack(fill="x")
        legend_help = self.label(
            legend_inner,
            "Hover over or select a status to see what it means.",
            8, COLORS["muted"], bg=COLORS["card"])
        legend_help.pack(anchor="w", pady=(5, 0))
        legend_definitions = [
            ("Pending", "Waiting for the administrator's decision."),
            ("Refunded", "Payment has been successfully refunded."),
            ("Rejected", "Reservation has been denied by the administrator."),
            ("Approved", "Reservation has been approved."),
            ("Completed", "Carwash service has been completed."),
        ]
        legend_widgets = []
        def show_legend_meaning(status, meaning):
            legend_help.configure(text=f"{status}: {meaning}")
        for status_name, meaning in legend_definitions:
            item = tk.Frame(legend_items_frame, bg=COLORS["card"], padx=5, pady=3,
                            highlightthickness=1, highlightbackground="#263c53")
            indicator = tk.Canvas(item, width=12, height=12, bg=COLORS["card"],
                                  highlightthickness=0, bd=0)
            indicator.create_oval(1, 1, 11, 11,
                                  fill=RESERVATION_STATUS_COLORS[status_name],
                                  outline=RESERVATION_STATUS_COLORS[status_name])
            indicator.pack(side="left", padx=(0, 6))
            label = tk.Label(item, text=status_name, bg=COLORS["card"],
                             fg="#FFFFFF", font=("Segoe UI", 9, "bold"), anchor="w")
            label.pack(side="left")
            for widget in (item, indicator, label):
                widget.bind("<Enter>", lambda _e, st=status_name, msg=meaning:
                            show_legend_meaning(st, msg))
                widget.bind("<Button-1>", lambda _e, st=status_name, msg=meaning:
                            show_legend_meaning(st, msg))
            legend_widgets.append(item)

        def layout_status_legend(event=None):
            available_width = max(1, legend_items_frame.winfo_width())
            # About 170 px per item keeps the row aligned without clipping.
            columns = max(1, min(len(legend_widgets), available_width // 170))
            for column in range(len(legend_widgets)):
                legend_items_frame.columnconfigure(column, weight=0, uniform="")
            for column in range(columns):
                legend_items_frame.columnconfigure(column, weight=1, uniform="status_legend")
            for index, item in enumerate(legend_widgets):
                item.grid(row=index // columns, column=index % columns,
                          sticky="ew", padx=(0, 5), pady=2)

        legend_items_frame.bind("<Configure>", layout_status_legend)

        controls_card = self.card(content)
        controls_card.pack(fill="x", pady=(0, 8))
        controls = tk.Frame(controls_card, bg=COLORS["card"])
        controls.pack(fill="x", padx=12, pady=(9, 3))
        controls.columnconfigure(3, weight=1)
        self.label(controls, "Filter", 9, COLORS["muted"], True).grid(
            row=0, column=0, sticky="w", padx=(0, 6))
        self.admin_filter = tk.StringVar(value=filter_value)
        filter_combo = ttk.Combobox(
            controls, textvariable=self.admin_filter,
            values=["All", "Pending", "Approved", "Rejected", "Completed", "Cancelled"],
            state="readonly", width=14,
        )
        filter_combo.grid(row=0, column=1, sticky="w")
        filter_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_admin_table())
        self.label(controls, "Search", 9, COLORS["muted"], True).grid(
            row=0, column=2, sticky="w", padx=(14, 6))
        self.admin_search = tk.StringVar(value=search_value)
        search_entry = tk.Entry(
            controls, textvariable=self.admin_search, width=18,
            font=("Segoe UI", 10), relief="flat", bg=COLORS["card2"], fg="white",
            insertbackground="white", highlightbackground="#2c4764",
            highlightthickness=1,
        )
        search_entry.grid(row=0, column=3, sticky="ew")
        search_entry.bind("<Return>", lambda _event: self.refresh_admin_table())
        self.btn(controls, "SEARCH / REFRESH", self.refresh_admin_table, width=17).grid(
            row=0, column=4, sticky="w", padx=(8, 0))
        self.label(controls_card, "Reservations refresh automatically every 5 seconds.",
                   8, COLORS["muted"], bg=COLORS["card"]).pack(
                       anchor="w", padx=14, pady=(0, 8))

        table_frame = self.card(content)
        table_frame.pack(fill="x", pady=(0, 10))
        cols = ("id", "customer", "contact", "vehicle", "service", "date", "time",
                "bay", "total", "paid", "balance", "status", "payment")
        self.configure_admin_table_style()
        self.admin_tree = ttk.Treeview(
            table_frame, columns=cols, show="headings", style="Admin.Treeview",
            selectmode="browse", height=8,
        )
        names = {
            "id": "Reservation ID", "customer": "Customer", "contact": "Contact",
            "vehicle": "Vehicle", "service": "Service", "date": "Date",
            "time": "Time", "bay": "Bay", "total": "Total", "paid": "Amount Paid",
            "balance": "Remaining Balance", "status": "Booking",
            "payment": "Payment Status",
        }
        widths = {
            "id": 100, "customer": 115, "contact": 100, "vehicle": 85,
            "service": 110, "date": 90, "time": 75, "bay": 60, "total": 80,
            "paid": 95, "balance": 105, "status": 80, "payment": 95,
        }
        for column in cols:
            self.admin_tree.heading(column, text=names[column])
            self.admin_tree.column(column, width=widths[column], anchor="center")
        self.admin_tree.tag_configure("pending", foreground=RESERVATION_STATUS_COLORS["Pending"])
        self.admin_tree.tag_configure("approved", foreground=RESERVATION_STATUS_COLORS["Approved"])
        self.admin_tree.tag_configure("rejected", foreground=RESERVATION_STATUS_COLORS["Rejected"])
        self.admin_tree.tag_configure("completed", foreground=RESERVATION_STATUS_COLORS["Completed"])
        self.admin_tree.tag_configure("refunded", foreground=RESERVATION_STATUS_COLORS["Refunded"])
        self.admin_tree.tag_configure("cancelled", foreground=COLORS["muted"])
        self.admin_tree.tag_configure("paid", foreground="#36d98a")
        self.admin_tree.tag_configure("partial", foreground="#ffd34d")
        self.admin_tree.tag_configure("unpaid", foreground=COLORS["muted"])
        self.admin_tree.tag_configure("odd", background="#0b1624")
        self.admin_tree.tag_configure("even", background="#101d2d")
        self.admin_tree.bind("<<TreeviewSelect>>",
                             lambda _event: self.update_admin_action_state())
        self.admin_tree.bind("<Double-1>", lambda _event: self.admin_view_selected())

        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.admin_tree.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 0))
        admin_v_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.admin_tree.yview)
        admin_h_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.admin_tree.xview)
        admin_v_scroll.grid(row=0, column=1, sticky="ns", pady=(8, 0))
        admin_h_scroll.grid(row=1, column=0, sticky="ew")
        self.admin_tree.configure(yscrollcommand=admin_v_scroll.set,
                                  xscrollcommand=admin_h_scroll.set)

        actions_panel = self.card(content)
        actions_panel.pack(fill="x", pady=(0, 10))
        actions = tk.Frame(actions_panel, bg=COLORS["card"])
        actions.pack(fill="x", padx=10, pady=(10, 4))
        approve_button = self.btn(actions, "✓  APPROVE SELECTED",
                                  lambda: self.admin_set_selected("Approved"))
        reject_button = self.btn(actions, "✕  REJECT SELECTED",
                                 lambda: self.admin_set_selected("Rejected"),
                                 primary=False)
        complete_button = self.btn(actions, "★  MARK COMPLETED",
                                   lambda: self.admin_set_selected("Completed"),
                                   primary=False)
        view_button = self.btn(actions, "◉  VIEW DETAILS / PAYMENT",
                               self.admin_view_selected)
        for button in (approve_button, reject_button, complete_button, view_button):
            button.configure(state="disabled", cursor="arrow",
                             disabledforeground="#6b7f95")
        reject_button.configure(bg="#B91C1C", fg="#FFFFFF",
                                activebackground="#991B1B", activeforeground="#FFFFFF")
        complete_button.configure(bg="#14352a", fg="#87e6b3",
                                  activebackground="#1c4d3a", activeforeground="white")
        self._admin_action_buttons = [approve_button, reject_button, complete_button, view_button]
        actions_layout = {"columns": None}

        def layout_action_buttons(event=None):
            width = event.width if event is not None else actions.winfo_width()
            columns = 4 if width >= 900 else (2 if width >= 600 else 1)
            if actions_layout["columns"] == columns:
                return
            actions_layout["columns"] = columns
            for column in range(4):
                actions.columnconfigure(column, weight=0)
            for column in range(columns):
                actions.columnconfigure(column, weight=1,
                                        uniform=f"admin_actions_{columns}")
            for index, button in enumerate(self._admin_action_buttons):
                button.grid(row=index // columns, column=index % columns,
                            sticky="ew", padx=4, pady=3)

        actions.bind("<Configure>", layout_action_buttons)
        layout_action_buttons()
        self.admin_action_hint = self.label(
            actions_panel, "Select a reservation to enable actions.", 8,
            COLORS["muted"], bg=COLORS["card"])
        self.admin_action_hint.pack(anchor="w", padx=18, pady=(0, 8))

        self.refresh_admin_table()
        self._admin_view_active = True
        self.schedule_admin_refresh()

    def show_admin_payment_verification(self):
        if not self.is_staff_or_admin():
            messagebox.showerror("Access Denied", "Staff or admin access is required.")
            return
        content = self.admin_shell(
            "Payment Verification",
            "Verify submitted GCash and bank transfer payments before approving them.",
            active="verification",
        )
        collection = self.store.payment_summary()
        self.label(content, "PAYMENT SUMMARY", 12, COLORS["cyan"], True,
                   bg=COLORS["bg"]).pack(anchor="w")
        summary = tk.Frame(content, bg=COLORS["bg"])
        summary.pack(fill="x", pady=(4, 9))
        metrics = [
            ("Total Collected", money(collection["total_collected"]), COLORS["cyan"]),
            ("Awaiting Verification",
             f"{money(collection['pending_amount'])} ({collection['pending_count']})",
             "#ffd34d"),
            ("Outstanding Balance", money(collection["outstanding"]), "#ff8a95"),
        ]
        for index, (label_text, value, color) in enumerate(metrics):
            cell = self.admin_payment_metric(summary, label_text, value, color)
            cell.grid(row=0, column=index, sticky="nsew", padx=4, pady=4)
            summary.columnconfigure(index, weight=1)

        controls = self.card(content)
        controls.pack(fill="x", pady=(0, 8))
        body = tk.Frame(controls, bg=COLORS["card"])
        body.pack(fill="x", padx=12, pady=10)
        self.label(body, "Status", 8, COLORS["muted"], True).grid(
            row=0, column=0, sticky="w", padx=(0, 6))
        status_var = tk.StringVar(value="Unverified")
        status_combo = ttk.Combobox(
            body, textvariable=status_var, state="readonly", width=14,
            values=["All", "Unverified", "Verified", "Rejected"])
        status_combo.grid(row=0, column=1, sticky="w")
        self.label(body, "Search", 8, COLORS["muted"], True).grid(
            row=0, column=2, sticky="w", padx=(14, 6))
        query_var = tk.StringVar()
        search_entry = self.form_entry(body, query_var, width=24)
        search_entry.grid(row=0, column=3, sticky="ew")
        body.columnconfigure(3, weight=1)
        self.btn(body, "SEARCH / REFRESH", lambda: draw_payments(), width=17).grid(
            row=0, column=4, padx=(8, 0))

        results = tk.Frame(content, bg=COLORS["bg"])
        results.pack(fill="both", expand=True)
        tree_ref = [None]

        def selected_payment():
            tree = tree_ref[0]
            if tree is None or not tree.winfo_exists() or not tree.selection():
                messagebox.showinfo("Select Payment", "Select a payment row first.")
                return None
            payment_id = tree.selection()[0]
            return next((row for row in self.store.all_payments()
                         if str(row.get("payment_id")) == str(payment_id)), None)

        def verify(status):
            payment = selected_payment()
            if not payment:
                return
            if status == "Verified" and not messagebox.askyesno(
                    "Verify Payment", "Mark this payment as verified?"):
                return
            ok, message = self.store.verify_payment(
                payment["payment_id"], status, self.current_user["username"])
            if not ok:
                messagebox.showerror("Payment Not Updated", message)
                return
            messagebox.showinfo("Payment Updated", message)
            self.show_admin_payment_verification()

        def approve():
            payment = selected_payment()
            if not payment:
                return
            ok, message = self.store.approve_payment(
                payment["payment_id"], self.current_user["username"])
            if not ok:
                messagebox.showerror("Payment Not Approved", message)
                return
            reservation = self.store.get_reservation(payment["reservation_id"])
            note = ""
            if reservation and reservation.get("status") == "Pending":
                self.store.set_booking_status(
                    payment["reservation_id"], "Approved", self.current_user["username"])
                note = "\n\nReservation approved."
            messagebox.showinfo("Payment Approved", message + note)
            self.show_admin_payment_verification()

        def open_booking():
            payment = selected_payment()
            if payment:
                self.show_reservation_details(payment["reservation_id"],
                                              customer_view=False)

        def open_image():
            payment = selected_payment()
            if payment:
                self.show_payment_image(payment)

        def draw_payments():
            for child in results.winfo_children():
                child.destroy()
            rows = self.store.all_payments(status_var.get(), query_var.get())
            if not rows:
                self.empty_state(results, "NO PAYMENTS FOUND",
                                 "No payment submissions match the selected filters.")
                return
            table_card = self.card(results)
            table_card.pack(fill="both", expand=True)
            self.configure_admin_table_style()
            columns = ("id", "reservation", "customer", "method", "amount",
                       "reference", "submitted", "verification", "approval")
            nonlocal_tree = ttk.Treeview(
                table_card, columns=columns, show="headings", style="Admin.Treeview",
                selectmode="browse", height=10)
            names = {
                "id": "Payment", "reservation": "Reservation", "customer": "Customer",
                "method": "Method", "amount": "Amount", "reference": "Reference",
                "submitted": "Submitted", "verification": "Verification",
                "approval": "Approval",
            }
            for column in columns:
                nonlocal_tree.heading(column, text=names[column])
                nonlocal_tree.column(column, width=155 if column == "reference" else 105,
                                     anchor="center")
            nonlocal_tree.tag_configure("unverified", foreground="#ffd34d")
            nonlocal_tree.tag_configure("verified", foreground="#36d98a")
            nonlocal_tree.tag_configure("rejected", foreground=RESERVATION_STATUS_COLORS["Rejected"])
            nonlocal_tree.tag_configure("odd", background="#0b1624")
            nonlocal_tree.tag_configure("even", background="#101d2d")
            for index, row in enumerate(rows):
                verification = str(row.get("verification_status") or "Unverified")
                nonlocal_tree.insert("", "end", iid=str(row["payment_id"]), values=(
                    f"PAY-{int(row['payment_id']):03d}", row.get("reservation_id"),
                    row.get("customer"), row.get("method"),
                    money(row.get("amount_submitted") or 0), row.get("reference") or "",
                    format_timestamp(row.get("submitted_at")), verification,
                    row.get("approval_status") or "—",
                ), tags=(verification.lower(),
                         "odd" if index % 2 == 0 else "even"))
            nonlocal_tree.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 0))
            table_card.rowconfigure(0, weight=1)
            table_card.columnconfigure(0, weight=1)
            v_scroll = ttk.Scrollbar(table_card, orient="vertical",
                                     command=nonlocal_tree.yview)
            h_scroll = ttk.Scrollbar(table_card, orient="horizontal",
                                     command=nonlocal_tree.xview)
            v_scroll.grid(row=0, column=1, sticky="ns", pady=(8, 0))
            h_scroll.grid(row=1, column=0, sticky="ew")
            nonlocal_tree.configure(yscrollcommand=v_scroll.set,
                                    xscrollcommand=h_scroll.set)
            nonlocal_tree.bind("<Double-1>", lambda _event: open_booking())
            actions = tk.Frame(results, bg=COLORS["bg"])
            actions.pack(fill="x", pady=(8, 0))
            action_specs = [
                ("VERIFY", lambda: verify("Verified"), True),
                ("REJECT", lambda: verify("Rejected"), False),
                ("APPROVE PAYMENT", approve, False),
                ("VIEW BOOKING", open_booking, False),
                ("VIEW REFERENCE IMAGE", open_image, False),
            ]
            action_buttons = [
                self.btn(actions, text, command, primary=primary)
                for text, command, primary in action_specs
            ]
            action_layout = {"columns": None}

            def layout_actions(event=None):
                width = event.width if event is not None else actions.winfo_width()
                columns = 3 if width >= 620 else 2
                if action_layout["columns"] == columns:
                    return
                action_layout["columns"] = columns
                for column in range(3):
                    actions.columnconfigure(column, weight=0)
                for column in range(columns):
                    actions.columnconfigure(column, weight=1,
                                            uniform=f"payment_actions_{columns}")
                for index, button in enumerate(action_buttons):
                    button.grid(row=index // columns, column=index % columns,
                                sticky="ew", padx=3, pady=3)

            actions.bind("<Configure>", layout_actions)
            layout_actions()
            tree_ref[0] = nonlocal_tree

        status_combo.bind("<<ComboboxSelected>>", lambda _event: draw_payments())
        search_entry.bind("<Return>", lambda _event: draw_payments())
        draw_payments()

    def show_admin_notifications(self):
        if not self.is_staff_or_admin():
            messagebox.showerror("Access Denied", "Staff or admin access is required.")
            return
        self.store.sync_admin_notifications()
        content = self.admin_shell(
            "Notifications",
            "Booking approvals, payments, and refunds that need attention.",
            active="notifications",
        )
        rows = self.store.notifications_for_user(self.current_user["username"])
        unread = sum(1 for row in rows if not row.get("read_at"))
        controls = tk.Frame(content, bg=COLORS["bg"])
        controls.pack(fill="x", pady=(0, 9))
        self.btn(controls, "REFRESH", self.show_admin_notifications,
                 primary=False, width=12).pack(side="left")
        if unread:
            self.btn(controls, "MARK ALL READ", self.mark_all_admin_notifications_read,
                     primary=False, width=16).pack(side="left", padx=8)
        self.label(controls, f"{unread} unread", 9, COLORS["yellow"], True,
                   bg=COLORS["bg"]).pack(side="right", padx=4)
        if not rows:
            self.empty_state(content, "NO NOTIFICATIONS",
                             "Operational alerts will appear here when bookings, payments, "
                             "or refunds need attention.")
            return
        for notification in rows:
            is_unread = not notification.get("read_at")
            card = self.card(content)
            card.pack(fill="x", pady=(0, 8))
            heading = tk.Frame(card, bg=COLORS["card"])
            heading.pack(fill="x", padx=18, pady=(11, 2))
            self.label(heading, notification.get("title") or "Notification", 11,
                       COLORS["yellow"] if is_unread else COLORS["white"], True).pack(
                           side="left")
            self.label(heading, "UNREAD" if is_unread else "READ", 8,
                       COLORS["yellow"] if is_unread else COLORS["muted"], True).pack(
                           side="right")
            self.label(card, notification.get("message") or "", 9, COLORS["muted"],
                       wraplength=380, justify="left").pack(
                           anchor="w", padx=18, pady=(0, 3))
            reservation_id = self._notification_reservation_id(notification)
            meta = [format_timestamp(notification.get("created_at"))]
            if reservation_id:
                meta.insert(0, f"Reservation {reservation_id}")
            self.label(card, "     ".join(meta), 8, COLORS["muted"]).pack(
                anchor="w", padx=18, pady=(0, 6))
            actions = tk.Frame(card, bg=COLORS["card"])
            actions.pack(fill="x", padx=18, pady=(0, 11))
            if reservation_id:
                self.btn(
                    actions, "OPEN RELATED BOOKING",
                    lambda nid=notification.get("notification_id"), rid=reservation_id:
                        self.open_admin_notification(nid, rid),
                    width=22,
                ).pack(side="right")
            if is_unread:
                self.btn(
                    actions, "MARK READ",
                    lambda nid=notification.get("notification_id"):
                        self.mark_admin_notification_read(nid),
                    primary=False, width=12,
                ).pack(side="right", padx=(0, 6))

    def open_admin_notification(self, notification_id, reservation_id):
        self.store.mark_notification_read(notification_id, self.current_user["username"])
        self.show_reservation_details(reservation_id, customer_view=False)

    def mark_admin_notification_read(self, notification_id):
        self.store.mark_notification_read(notification_id, self.current_user["username"])
        self.show_admin_notifications()

    def mark_all_admin_notifications_read(self):
        self.store.mark_all_notifications_read(self.current_user["username"])
        self.show_admin_notifications()

    def show_admin_reports(self):
        if not self.is_admin_user():
            messagebox.showerror("Access Denied", "Administrator access is required.")
            return
        content = self.admin_shell(
            "Reports",
            "Business performance by booking status, service, month, and collection.",
            active="reports",
        )
        stats = self.store.report_statistics()
        controls = tk.Frame(content, bg=COLORS["bg"])
        controls.pack(fill="x", pady=(0, 9))
        self.btn(controls, "EXPORT CSV REPORT", self.export_business_report, width=18).pack(
            side="left", padx=(0, 6))
        self.btn(controls, "REFRESH", self.show_admin_reports,
                 primary=False, width=12).pack(side="left")

        summary = tk.Frame(content, bg=COLORS["bg"])
        summary.pack(fill="x", pady=(0, 10))
        metrics = [
            ("Total Bookings", stats["counts"]["total"], COLORS["cyan"]),
            ("Completed Bookings", stats["counts"]["completed"], "#36d98a"),
            ("Total Collected", money(stats["collection"]["total_collected"]), COLORS["cyan"]),
            ("Refunds Completed", money(stats["refunded"]), "#ff9f6b"),
            ("Net Collected", money(stats["net_collected"]), "#8fb8ff"),
            ("Outstanding", money(stats["collection"]["outstanding"]), "#ff8a95"),
        ]
        for index, (name, value, color) in enumerate(metrics):
            cell = self.admin_payment_metric(summary, name, value, color)
            cell.grid(row=index // 3, column=index % 3, sticky="nsew", padx=4, pady=4)
            summary.columnconfigure(index % 3, weight=1)

        service_card = self.card(content)
        service_card.pack(fill="x", pady=(0, 10))
        self.label(service_card, "BOOKINGS BY SERVICE", 12, COLORS["cyan"], True).pack(
            anchor="w", padx=16, pady=(10, 6))
        self.configure_admin_table_style()
        service_tree = ttk.Treeview(
            service_card, columns=("service", "bookings", "value"), show="headings",
            style="Admin.Treeview", height=len(stats["services"]) or 1)
        for column, heading, width in (("service", "Service", 320),
                                       ("bookings", "Bookings", 110),
                                       ("value", "Booking Value", 150)):
            service_tree.heading(column, text=heading)
            service_tree.column(column, width=width,
                                anchor="w" if column == "service" else "center")
        for service, bucket in stats["services"].items():
            service_tree.insert("", "end", values=(
                service, bucket["count"], money(bucket["value"])))
        service_tree.pack(fill="x", padx=10, pady=(0, 10))
        if not stats["services"]:
            self.label(service_card, "No service statistics available.", 9,
                       COLORS["muted"]).pack(anchor="w", padx=16, pady=(0, 12))

        month_card = self.card(content)
        month_card.pack(fill="x", pady=(0, 10))
        self.label(month_card, "BOOKINGS BY MONTH (BOOKING DATE)", 12, COLORS["cyan"],
                   True).pack(anchor="w", padx=16, pady=(10, 6))
        month_tree = ttk.Treeview(
            month_card, columns=("month", "bookings", "value"), show="headings",
            style="Admin.Treeview", height=min(len(stats["months"]), 8) or 1)
        for column, heading, width in (("month", "Month", 220),
                                       ("bookings", "Bookings", 110),
                                       ("value", "Booking Value", 150)):
            month_tree.heading(column, text=heading)
            month_tree.column(column, width=width,
                              anchor="w" if column == "month" else "center")
        for month, bucket in stats["months"].items():
            month_tree.insert("", "end", values=(
                month, bucket["count"], money(bucket["value"])))
        month_tree.pack(fill="x", padx=10, pady=(0, 10))
        if not stats["months"]:
            self.label(month_card, "No monthly statistics available.", 9,
                       COLORS["muted"]).pack(anchor="w", padx=16, pady=(0, 12))

    def export_business_report(self):
        path = filedialog.asksaveasfilename(
            parent=self, title="Export Business Report", defaultextension=".csv",
            initialfile=f"washapp_report_{business_now().strftime('%Y%m%d_%H%M%S')}.csv",
            filetypes=[("CSV files", "*.csv")],
        )
        if not path:
            return
        try:
            self.store.export_report_csv(path)
        except OSError as error:
            messagebox.showerror("Export Failed", str(error))
            return
        messagebox.showinfo("Export Complete", f"Business report exported to:\n{path}")

    def show_admin_settings(self):
        if not self.is_admin_user():
            messagebox.showerror("Access Denied", "Administrator access is required.")
            return
        content = self.admin_shell(
            "System Settings",
            "Customize the branding shown across the reservation system.",
            active="settings",
        )
        settings = self.store.settings()
        form = self.card(content)
        form.pack(fill="x", pady=(0, 10))
        body = tk.Frame(form, bg=COLORS["card"])
        body.pack(fill="x", padx=18, pady=16)
        variables = {}
        fields = [
            ("system_name", "System Name"),
            ("system_subtitle", "System Subtitle"),
            ("tagline", "Tagline"),
            ("business_name", "Business Name"),
            ("contact_info", "Contact Information"),
        ]
        for row, (key, label_text) in enumerate(fields):
            variable = tk.StringVar(value=settings.get(key, ""))
            variables[key] = variable
            self.label(body, label_text.upper(), 9, COLORS["muted"], True).grid(
                row=row, column=0, sticky="w", padx=(0, 16), pady=6)
            entry = self.form_entry(body, variable, width=48)
            entry.grid(row=row, column=1, sticky="ew", pady=6, ipady=6)
        body.columnconfigure(1, weight=1)
        preview = self.card(content)
        preview.pack(fill="x", pady=(0, 10))
        self.label(preview, "LIVE BRANDING PREVIEW", 8, COLORS["muted"], True).pack(
            anchor="w", padx=18, pady=(12, 2))
        name_label = self.label(preview, "", 22, COLORS["cyan"], True)
        name_label.pack(anchor="w", padx=18)
        subtitle_label = self.label(preview, "", 8, COLORS["muted"], True)
        subtitle_label.pack(anchor="w", padx=18)
        tagline_label = self.label(preview, "", 10, COLORS["white"])
        tagline_label.pack(anchor="w", padx=18, pady=(8, 4))
        business_label = self.label(preview, "", 9, COLORS["muted"])
        business_label.pack(anchor="w", padx=18, pady=(0, 4))
        contact_label = self.label(preview, "", 9, COLORS["muted"])
        contact_label.pack(anchor="w", padx=18, pady=(0, 14))

        def refresh_preview(*_):
            name_label.configure(text=variables["system_name"].get().upper() or "WASHAPP")
            subtitle_label.configure(text=variables["system_subtitle"].get().upper())
            tagline_label.configure(text=variables["tagline"].get())
            business_label.configure(text=variables["business_name"].get())
            contact_label.configure(
                text=(f"Contact: {variables['contact_info'].get()}"
                      if variables["contact_info"].get().strip() else ""))
        for variable in variables.values():
            variable.trace_add("write", refresh_preview)
        refresh_preview()

        controls = tk.Frame(content, bg=COLORS["bg"])
        controls.pack(fill="x")
        self.btn(controls, "SAVE BRANDING", lambda: save_settings(), width=17).pack(
            side="left", padx=(0, 6))
        self.btn(controls, "RESET DEFAULTS", lambda: reset_settings(),
                 primary=False, width=16).pack(side="left")

        def save_settings():
            values = {key: variable.get() for key, variable in variables.items()}
            ok, message = self.store.save_settings(
                values, self.current_user["username"])
            if not ok:
                messagebox.showerror("Settings Not Saved", message)
                return
            messagebox.showinfo("Settings Saved", message)
            self.show_admin_settings()

        def reset_settings():
            if not messagebox.askyesno("Reset Branding",
                                       "Restore the default WashApp branding?"):
                return
            ok, message = self.store.reset_settings(self.current_user["username"])
            if not ok:
                messagebox.showerror("Reset Failed", message)
                return
            messagebox.showinfo("Branding Reset", message)
            self.show_admin_settings()

    def show_admin_accounts(self):
        if not self.is_admin_user():
            messagebox.showerror("Access Denied", "Administrator access is required.")
            return
        content = self.admin_shell(
            "Accounts",
            "Create customer and staff accounts, update roles, and control access.",
            active="accounts",
        )
        filter_state = getattr(self, "_account_filters",
                               {"role": "All", "status": "All", "query": ""})
        controls = self.card(content)
        controls.pack(fill="x", pady=(0, 8))
        body = tk.Frame(controls, bg=COLORS["card"])
        body.pack(fill="x", padx=12, pady=10)
        self.label(body, "Role", 8, COLORS["muted"], True).grid(
            row=0, column=0, sticky="w", padx=(0, 5))
        role_var = tk.StringVar(value=filter_state["role"])
        role_combo = ttk.Combobox(body, textvariable=role_var, state="readonly",
                                  width=12, values=["All", "customer", "staff", "admin"])
        role_combo.grid(row=0, column=1, sticky="w")
        self.label(body, "Status", 8, COLORS["muted"], True).grid(
            row=0, column=2, sticky="w", padx=(12, 5))
        status_var = tk.StringVar(value=filter_state["status"])
        status_combo = ttk.Combobox(body, textvariable=status_var, state="readonly",
                                    width=12, values=["All", "Active", "Inactive"])
        status_combo.grid(row=0, column=3, sticky="w")
        self.label(body, "Search", 8, COLORS["muted"], True).grid(
            row=0, column=4, sticky="w", padx=(12, 5))
        query_var = tk.StringVar(value=filter_state["query"])
        query_entry = self.form_entry(body, query_var, width=20)
        query_entry.grid(row=0, column=5, sticky="ew")
        body.columnconfigure(5, weight=1)
        self.btn(body, "FILTER", lambda: draw_accounts(), width=10).grid(
            row=0, column=6, padx=(8, 0))

        results = tk.Frame(content, bg=COLORS["bg"])
        results.pack(fill="both", expand=True)
        tree_ref = [None]

        def selected_account():
            tree = tree_ref[0]
            if tree is None or not tree.winfo_exists() or not tree.selection():
                messagebox.showinfo("Select Account", "Select an account row first.")
                return None
            username = tree.selection()[0]
            return self.store.user(username)

        def new_account():
            self.open_account_dialog(None)

        def edit_account():
            user = selected_account()
            if user:
                self.open_account_dialog(user["username"])

        def reset_password():
            user = selected_account()
            if user:
                self.open_password_reset_dialog(user["username"])

        def toggle_status():
            user = selected_account()
            if not user:
                return
            new_status = "Inactive" if (user.get("status") or "Active") == "Active" else "Active"
            if not messagebox.askyesno(
                    "Change Account Status",
                    f"Set {user['username']} to {new_status}?"):
                return
            ok, message = self.store.set_user_status(
                self.current_user["username"], user["username"], new_status)
            if not ok:
                messagebox.showerror("Account Not Updated", message)
                return
            messagebox.showinfo("Account Updated", message)
            self._account_filters = {"role": role_var.get(), "status": status_var.get(),
                                     "query": query_var.get()}
            self.show_admin_accounts()

        def draw_accounts():
            self._account_filters = {"role": role_var.get(), "status": status_var.get(),
                                     "query": query_var.get()}
            for child in results.winfo_children():
                child.destroy()
            rows = self.store.users_for_management(
                role_var.get(), status_var.get(), query_var.get())
            if not rows:
                self.empty_state(results, "NO ACCOUNTS FOUND",
                                 "No accounts match the selected filters.")
                return
            table_card = self.card(results)
            table_card.pack(fill="both", expand=True)
            self.configure_admin_table_style()
            columns = ("username", "name", "role", "status", "email", "contact", "created")
            tree = ttk.Treeview(table_card, columns=columns, show="headings",
                                style="Admin.Treeview", selectmode="browse", height=11)
            names = {
                "username": "Username", "name": "Full Name", "role": "Role",
                "status": "Status", "email": "Email", "contact": "Contact",
                "created": "Created",
            }
            for column in columns:
                tree.heading(column, text=names[column])
                tree.column(column, width=170 if column in ("name", "email") else 100,
                            anchor="w" if column in ("name", "email") else "center")
            tree.tag_configure("active", foreground="#36d98a")
            tree.tag_configure("inactive", foreground=COLORS["muted"])
            tree.tag_configure("odd", background="#0b1624")
            tree.tag_configure("even", background="#101d2d")
            for index, row in enumerate(rows):
                tree.insert("", "end", iid=row["username"], values=(
                    row["username"], row.get("full_name") or "", row.get("role") or "",
                    row.get("status") or "Active", row.get("email") or "—",
                    row.get("contact") or "—",
                    format_timestamp(row.get("created_at")),
                ), tags=(str(row.get("status") or "Active").lower(),
                         "odd" if index % 2 == 0 else "even"))
            tree.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 0))
            table_card.rowconfigure(0, weight=1)
            table_card.columnconfigure(0, weight=1)
            v_scroll = ttk.Scrollbar(table_card, orient="vertical", command=tree.yview)
            h_scroll = ttk.Scrollbar(table_card, orient="horizontal", command=tree.xview)
            v_scroll.grid(row=0, column=1, sticky="ns", pady=(8, 0))
            h_scroll.grid(row=1, column=0, sticky="ew")
            tree.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)
            tree.bind("<Double-1>", lambda _event: edit_account())
            tree_ref[0] = tree
            actions = tk.Frame(results, bg=COLORS["bg"])
            actions.pack(fill="x", pady=(8, 0))
            action_specs = [
                ("NEW ACCOUNT", new_account, True),
                ("EDIT SELECTED", edit_account, False),
                ("RESET PASSWORD", reset_password, False),
                ("ACTIVATE / DEACTIVATE", toggle_status, False),
            ]
            action_buttons = [
                self.btn(actions, text, command, primary=primary)
                for text, command, primary in action_specs
            ]
            action_layout = {"columns": None}

            def layout_actions(event=None):
                width = event.width if event is not None else actions.winfo_width()
                columns = 4 if width >= 700 else 2
                if action_layout["columns"] == columns:
                    return
                action_layout["columns"] = columns
                for column in range(4):
                    actions.columnconfigure(column, weight=0)
                for column in range(columns):
                    actions.columnconfigure(column, weight=1,
                                            uniform=f"account_actions_{columns}")
                for index, button in enumerate(action_buttons):
                    button.grid(row=index // columns, column=index % columns,
                                sticky="ew", padx=3, pady=3)

            actions.bind("<Configure>", layout_actions)
            layout_actions()

        role_combo.bind("<<ComboboxSelected>>", lambda _event: draw_accounts())
        status_combo.bind("<<ComboboxSelected>>", lambda _event: draw_accounts())
        query_entry.bind("<Return>", lambda _event: draw_accounts())
        draw_accounts()

    def open_account_dialog(self, username=None):
        account = self.store.user(username) if username else None
        dialog = tk.Toplevel(self)
        dialog.title("Edit Account" if account else "New Account")
        dialog.configure(bg=COLORS["bg"])
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        panel = self.card(dialog)
        panel.pack(fill="both", expand=True, padx=18, pady=18)
        self.label(panel, "EDIT ACCOUNT" if account else "CREATE ACCOUNT", 14,
                   COLORS["cyan"], True).pack(anchor="w", padx=20, pady=(16, 8))
        variables = {}
        field_specs = [
            ("username", "Username", 28),
            ("full_name", "Full Name", 34),
            ("contact", "Contact Number", 28),
            ("email", "Email", 34),
        ]
        for key, label_text, width in field_specs:
            self.label(panel, label_text.upper(), 9, COLORS["muted"], True).pack(
                anchor="w", padx=20, pady=(5, 2))
            variable = tk.StringVar(value=(account or {}).get(key) or "")
            variables[key] = variable
            entry = self.form_entry(panel, variable, width=width)
            entry.pack(fill="x", padx=20, ipady=6)
            if account and key == "username":
                entry.configure(state="readonly", disabledbackground=COLORS["card"],
                                disabledforeground=COLORS["muted"])
        role_var = tk.StringVar(value=(account or {}).get("role") or "customer")
        status_var = tk.StringVar(value=(account or {}).get("status") or "Active")
        row = tk.Frame(panel, bg=COLORS["card"])
        row.pack(fill="x", padx=20, pady=(8, 0))
        self.label(row, "ROLE", 9, COLORS["muted"], True).pack(side="left")
        ttk.Combobox(row, textvariable=role_var, state="readonly", width=14,
                     values=list(ACCOUNT_ROLES)).pack(side="left", padx=(6, 14))
        self.label(row, "STATUS", 9, COLORS["muted"], True).pack(side="left")
        ttk.Combobox(row, textvariable=status_var, state="readonly", width=12,
                     values=list(ACCOUNT_STATUSES)).pack(side="left", padx=6)
        password_var = tk.StringVar()
        if not account:
            self.label(panel, "TEMPORARY PASSWORD", 9, COLORS["muted"], True).pack(
                anchor="w", padx=20, pady=(10, 2))
            self.form_entry(panel, password_var, width=32, show="*").pack(
                fill="x", padx=20, ipady=6)
            self.label(panel, "The user should change this password after signing in.",
                       8, COLORS["muted"], bg=COLORS["card"]).pack(
                           anchor="w", padx=20, pady=(2, 0))

        def save():
            if account:
                ok, message = self.store.update_user_account(
                    self.current_user["username"], account["username"],
                    variables["full_name"].get(), variables["contact"].get(),
                    variables["email"].get(), role_var.get(), status_var.get())
            else:
                ok, message = self.store.create_user(
                    self.current_user["username"], variables["username"].get(),
                    password_var.get(), variables["full_name"].get(),
                    variables["contact"].get(), variables["email"].get(),
                    role_var.get())
            if not ok:
                messagebox.showerror("Account Not Saved", message, parent=dialog)
                return
            messagebox.showinfo("Account Saved", message, parent=dialog)
            dialog.destroy()
            self.show_admin_accounts()

        controls = tk.Frame(panel, bg=COLORS["card"])
        controls.pack(fill="x", padx=20, pady=(16, 16))
        self.btn(controls, "SAVE ACCOUNT", save, width=15).pack(
            side="left", padx=(0, 6))
        self.btn(controls, "CANCEL", dialog.destroy, primary=False,
                 width=10).pack(side="left")

    def open_password_reset_dialog(self, username):
        dialog = tk.Toplevel(self)
        dialog.title("Reset Password")
        dialog.configure(bg=COLORS["bg"])
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        panel = self.card(dialog)
        panel.pack(fill="both", expand=True, padx=18, pady=18)
        self.label(panel, "RESET PASSWORD", 14, COLORS["cyan"], True).pack(
            anchor="w", padx=20, pady=(16, 3))
        self.label(panel, f"Account: {username}", 9, COLORS["muted"]).pack(
            anchor="w", padx=20, pady=(0, 8))
        password_var = tk.StringVar()
        confirm_var = tk.StringVar()
        for label_text, variable in (("NEW PASSWORD", password_var),
                                     ("CONFIRM NEW PASSWORD", confirm_var)):
            self.label(panel, label_text, 9, COLORS["muted"], True).pack(
                anchor="w", padx=20, pady=(5, 2))
            self.form_entry(panel, variable, width=28, show="*").pack(
                fill="x", padx=20, ipady=6)

        def save():
            if password_var.get() != confirm_var.get():
                messagebox.showerror("Password Not Changed",
                                     "The new passwords do not match.", parent=dialog)
                return
            ok, message = self.store.reset_user_password(
                self.current_user["username"], username, password_var.get())
            if not ok:
                messagebox.showerror("Password Not Changed", message, parent=dialog)
                return
            messagebox.showinfo("Password Reset", message, parent=dialog)
            dialog.destroy()

        controls = tk.Frame(panel, bg=COLORS["card"])
        controls.pack(fill="x", padx=20, pady=(16, 16))
        self.btn(controls, "RESET PASSWORD", save, width=15).pack(
            side="left", padx=(0, 6))
        self.btn(controls, "CANCEL", dialog.destroy, primary=False,
                 width=10).pack(side="left")

    def show_admin_services(self):
        if not self.is_admin_user():
            messagebox.showerror("Access Denied", "Administrator access is required.")
            return
        content = self.admin_shell(
            "Services & Pricing",
            "Manage the service catalog used by new customer bookings.",
            active="services",
        )
        controls = tk.Frame(content, bg=COLORS["bg"])
        controls.pack(fill="x", pady=(0, 9))
        self.btn(controls, "ADD SERVICE", lambda: self.open_service_dialog(None),
                 width=14).pack(side="left", padx=(0, 6))
        self.btn(controls, "REFRESH", self.show_admin_services,
                 primary=False, width=12).pack(side="left")
        self.label(controls, "Applies to new bookings only.",
                   8, COLORS["muted"], bg=COLORS["bg"]).pack(side="right")

        services = self.store.services()
        if not services:
            self.empty_state(content, "NO SERVICES",
                             "Add at least one available service before accepting bookings.")
            return
        for service in services:
            card = self.card(content)
            card.pack(fill="x", pady=(0, 8))
            head = tk.Frame(card, bg=COLORS["card"])
            head.pack(fill="x", padx=16, pady=(10, 3))
            self.label(head, service["name"], 12, COLORS["white"], True).pack(
                side="left")
            available = bool(service.get("available"))
            self.label(head, "AVAILABLE" if available else "UNAVAILABLE", 8,
                       "#36d98a" if available else "#ff8a95", True).pack(
                           side="right", padx=(8, 0))
            self.label(head, service.get("service_id") or "", 8, COLORS["muted"],
                       True).pack(side="right", padx=(0, 12))
            self.label(card, service.get("description") or "No description provided.",
                       9, COLORS["muted"], wraplength=720, justify="left").pack(
                           anchor="w", padx=16)
            prices = tk.Frame(card, bg=COLORS["card"])
            prices.pack(fill="x", padx=12, pady=(7, 3))
            for index, vehicle in enumerate(VEHICLES):
                cell = tk.Frame(prices, bg=COLORS["card2"],
                                highlightbackground="#23374e", highlightthickness=1)
                cell.grid(row=0, column=index, sticky="nsew", padx=4, pady=4)
                prices.columnconfigure(index, weight=1)
                self.label(cell, vehicle.upper(), 8, COLORS["muted"], True,
                           bg=COLORS["card2"]).pack(anchor="w", padx=10, pady=(7, 1))
                shown_price = (money(service["prices"].get(vehicle, 0))
                               if vehicle in SERVICE_PRICE_DEFAULTS.get(service["name"], {})
                               else "—")
                self.label(cell, shown_price, 11, COLORS["cyan"], True,
                           bg=COLORS["card2"]).pack(anchor="w", padx=10, pady=(0, 7))
            actions = tk.Frame(card, bg=COLORS["card"])
            actions.pack(fill="x", padx=14, pady=(0, 10))
            self.btn(actions, "EDIT SERVICE",
                     lambda sid=service["service_id"]: self.open_service_dialog(sid),
                     width=14).pack(side="left", padx=(0, 5))
            toggle_text = "MARK UNAVAILABLE" if available else "MARK AVAILABLE"
            self.btn(actions, toggle_text,
                     lambda sid=service["service_id"], value=not available:
                         self.toggle_service_availability(sid, value),
                     primary=False, width=17).pack(side="left")

    def toggle_service_availability(self, service_id, available):
        ok, message = self.store.set_service_availability(
            self.current_user["username"], service_id, available)
        if not ok:
            messagebox.showerror("Service Not Updated", message)
            return
        messagebox.showinfo("Service Updated", message)
        self.show_admin_services()

    def open_service_dialog(self, service_id):
        service = self.store.service(service_id) if service_id else None
        dialog = tk.Toplevel(self)
        dialog.title("Edit Service" if service else "Add Service")
        dialog.configure(bg=COLORS["bg"])
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        panel = self.card(dialog)
        panel.pack(fill="both", expand=True, padx=18, pady=18)
        self.label(panel, "EDIT SERVICE & PRICING" if service else "ADD SERVICE & PRICING",
                   14, COLORS["cyan"], True).pack(anchor="w", padx=20, pady=(16, 8))
        name_var = tk.StringVar(value=(service or {}).get("name") or "")
        description_var = tk.StringVar(value=(service or {}).get("description") or "")
        for label_text, variable in (("SERVICE NAME", name_var),
                                     ("DESCRIPTION", description_var)):
            self.label(panel, label_text, 9, COLORS["muted"], True).pack(
                anchor="w", padx=20, pady=(5, 2))
            self.form_entry(panel, variable, width=42).pack(
                fill="x", padx=20, ipady=6)
        price_vars = {}
        price_grid = tk.Frame(panel, bg=COLORS["card"])
        price_grid.pack(fill="x", padx=20, pady=(10, 0))
        for index, vehicle in enumerate(VEHICLES):
            self.label(price_grid, f"{vehicle.upper()} PRICE (PHP)", 8,
                       COLORS["muted"], True).grid(
                           row=0, column=index, sticky="w", padx=(0, 8), pady=(0, 2))
            supported = vehicle in SERVICE_PRICE_DEFAULTS.get(name_var.get(), {v: 0 for v in VEHICLES})
            stored_price = (service or {}).get("prices", {}).get(vehicle, "")
            variable = tk.StringVar(value=f"{stored_price}" if supported else "N/A")
            price_vars[vehicle] = variable
            entry = self.form_entry(price_grid, variable, width=16)
            entry.grid(row=1, column=index, sticky="ew", padx=(0, 8), ipady=5)
            if not supported:
                entry.configure(state="disabled")
            price_grid.columnconfigure(index, weight=1)
        available_var = tk.BooleanVar(
            value=bool((service or {}).get("available", True)))
        tk.Checkbutton(
            panel, text="Available for new bookings", variable=available_var,
            font=("Segoe UI", 9), fg="white", bg=COLORS["card"],
            activebackground=COLORS["card"], activeforeground="white",
            selectcolor=COLORS["card2"], cursor="hand2",
        ).pack(anchor="w", padx=20, pady=(10, 0))

        def save():
            supported = SERVICE_PRICE_DEFAULTS.get(
                name_var.get().strip(), {vehicle: 0 for vehicle in VEHICLES})
            prices = {vehicle: variable.get() for vehicle, variable in price_vars.items()
                      if vehicle in supported}
            ok, message = self.store.save_service(
                self.current_user["username"], service_id,
                name_var.get(), description_var.get(), prices, available_var.get())
            if not ok:
                messagebox.showerror("Service Not Saved", message, parent=dialog)
                return
            messagebox.showinfo("Service Saved", message, parent=dialog)
            dialog.destroy()
            self.show_admin_services()

        controls = tk.Frame(panel, bg=COLORS["card"])
        controls.pack(fill="x", padx=20, pady=(16, 16))
        self.btn(controls, "SAVE SERVICE", save, width=15).pack(
            side="left", padx=(0, 6))
        self.btn(controls, "CANCEL", dialog.destroy, primary=False,
                 width=10).pack(side="left")

    def show_admin_refunds(self, reservation_id=None):
        if not self.is_admin_user():
            messagebox.showerror("Access Denied", "Administrator access is required.")
            return
        content = self.admin_shell(
            "Refunds",
            "Review refund requests, customer destinations, and completed payouts.",
            active="refunds",
        )
        controls = self.card(content)
        controls.pack(fill="x", pady=(0, 8))
        body = tk.Frame(controls, bg=COLORS["card"])
        body.pack(fill="x", padx=12, pady=10)
        self.label(body, "Status", 8, COLORS["muted"], True).grid(
            row=0, column=0, sticky="w", padx=(0, 5))
        status_var = tk.StringVar(value="All")
        status_combo = ttk.Combobox(
            body, textvariable=status_var, state="readonly", width=13,
            values=["All", "Pending", "Processing", "Completed", "Cancelled"])
        status_combo.grid(row=0, column=1, sticky="w")
        self.label(body, "Search", 8, COLORS["muted"], True).grid(
            row=0, column=2, sticky="w", padx=(14, 5))
        query_var = tk.StringVar()
        query_entry = self.form_entry(body, query_var, width=22)
        query_entry.grid(row=0, column=3, sticky="ew")
        body.columnconfigure(3, weight=1)
        self.btn(body, "FILTER", lambda: draw_refunds(), width=10).grid(
            row=0, column=4, padx=(8, 0))
        results = tk.Frame(content, bg=COLORS["bg"])
        results.pack(fill="both", expand=True)
        tree_ref = [None]

        def selected_refund():
            tree = tree_ref[0]
            if tree is None or not tree.winfo_exists() or not tree.selection():
                messagebox.showinfo("Select Refund", "Select a refund row first.")
                return None
            refund_id = tree.selection()[0]
            return self.store.get_refund(refund_id)

        def open_booking():
            refund = selected_refund()
            if refund:
                self.show_reservation_details(
                    refund.get("reservation_id"), customer_view=False)

        def record_refund():
            refund = selected_refund()
            if refund:
                self.admin_update_refund(
                    refund["refund_id"], refund.get("reservation_id"))

        def draw_refunds():
            for child in results.winfo_children():
                child.destroy()
            rows = self.store.all_refunds(status_var.get(), query_var.get())
            if not rows:
                self.empty_state(results, "NO REFUNDS FOUND",
                                 "No refund requests match the selected filters.")
                return
            table_card = self.card(results)
            table_card.pack(fill="both", expand=True)
            self.configure_admin_table_style()
            columns = ("id", "reservation", "customer", "amount", "method",
                       "destination", "status", "reference", "updated")
            tree = ttk.Treeview(table_card, columns=columns, show="headings",
                                style="Admin.Treeview", selectmode="extended", height=10)
            names = {
                "id": "Refund", "reservation": "Reservation", "customer": "Customer",
                "amount": "Amount", "method": "Payment Method", "destination": "Refund To",
                "status": "Status", "reference": "Reference", "updated": "Updated",
            }
            for column in columns:
                tree.heading(column, text=names[column])
                tree.column(column, width=145 if column == "destination" else 100,
                            anchor="center")
            tree.tag_configure("pending", foreground=RESERVATION_STATUS_COLORS["Pending"])
            tree.tag_configure("processing", foreground=COLORS["cyan"])
            tree.tag_configure("completed", foreground="#36d98a")
            tree.tag_configure("cancelled", foreground=COLORS["muted"])
            tree.tag_configure("odd", background="#0b1624")
            tree.tag_configure("even", background="#101d2d")
            for index, row in enumerate(rows):
                destination = row.get("refund_method") or "Not provided"
                if row.get("account_holder"):
                    destination += f" • {row['account_holder']}"
                tree.insert("", "end", iid=row["refund_id"], values=(
                    row["refund_id"], row.get("reservation_id"),
                    row.get("customer") or row.get("username") or "",
                    money(row.get("amount") or 0),
                    self._refund_payment_method_text(row), destination,
                    row.get("status") or "Pending", row.get("reference") or "—",
                    format_timestamp(row.get("updated_at")),
                ), tags=(str(row.get("status") or "Pending").lower(),
                         "odd" if index % 2 == 0 else "even"))
            tree.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 0))
            table_card.rowconfigure(0, weight=1)
            table_card.columnconfigure(0, weight=1)
            v_scroll = ttk.Scrollbar(table_card, orient="vertical", command=tree.yview)
            h_scroll = ttk.Scrollbar(table_card, orient="horizontal", command=tree.xview)
            v_scroll.grid(row=0, column=1, sticky="ns", pady=(8, 0))
            h_scroll.grid(row=1, column=0, sticky="ew")
            tree.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)
            tree.bind("<Double-1>", lambda _event: open_booking())
            tree_ref[0] = tree
            actions = tk.Frame(results, bg=COLORS["bg"])
            actions.pack(fill="x", pady=(8, 0))
            self.btn(actions, "OPEN BOOKING", open_booking, width=14).pack(
                side="left", padx=(0, 5))
            self.btn(actions, "RECORD / UPDATE REFUND", record_refund,
                     primary=False, width=21).pack(side="left")

        status_combo.bind("<<ComboboxSelected>>", lambda _event: draw_refunds())
        query_entry.bind("<Return>", lambda _event: draw_refunds())
        if reservation_id:
            query_var.set(str(reservation_id))
        draw_refunds()
        if reservation_id:
            refund = self.store.refund_for_reservation(reservation_id)
            if refund and refund.get("status") != "Completed":
                self.after_idle(lambda ref=refund, rid=reservation_id:
                                self.admin_update_refund(ref["refund_id"], rid))

    def show_admin_booking_history(self):
        if not self.is_staff_or_admin():
            messagebox.showerror("Access Denied", "Staff or admin access is required.")
            return
        content = self.admin_shell(
            "Booking History",
            "Filter booking records by booking date, customer, and status.",
            active="history",
        )
        filters = self.card(content)
        filters.pack(fill="x", pady=(0, 9))
        body = tk.Frame(filters, bg=COLORS["card"])
        body.pack(fill="x", padx=14, pady=12)
        self.label(body, "DATE RANGE AND CUSTOMER", 8, COLORS["cyan"], True).grid(
            row=0, column=0, columnspan=5, sticky="w", pady=(0, 6))
        start_var, start_field = self.date_filter_field(body, "FROM (MM/DD/YYYY)")
        start_field.grid(row=1, column=0, sticky="w", padx=(0, 10))
        end_var, end_field = self.date_filter_field(body, "TO (MM/DD/YYYY)")
        end_field.grid(row=1, column=1, sticky="w")
        customer_var = tk.StringVar()
        customer_field = tk.Frame(body, bg=COLORS["card"])
        customer_field.grid(row=2, column=0, sticky="w", padx=(0, 10), pady=(8, 0))
        self.label(customer_field, "CUSTOMER / RESERVATION", 8, COLORS["muted"],
                   True).pack(anchor="w")
        customer_entry = self.form_entry(customer_field, customer_var, width=22)
        customer_entry.pack(anchor="w", pady=(3, 0), ipady=6)
        status_var = tk.StringVar(value="All")
        status_field = tk.Frame(body, bg=COLORS["card"])
        status_field.grid(row=2, column=1, sticky="w", pady=(8, 0))
        self.label(status_field, "BOOKING STATUS", 8, COLORS["muted"], True).pack(
            anchor="w")
        status_combo = ttk.Combobox(
            status_field, textvariable=status_var, state="readonly", width=14,
            values=["All", "Pending", "Approved", "Rejected", "Completed", "Cancelled"])
        status_combo.pack(anchor="w", pady=(3, 0))
        buttons = tk.Frame(body, bg=COLORS["card"])
        buttons.grid(row=3, column=0, columnspan=2, sticky="w", pady=(9, 0))
        self.btn(buttons, "APPLY FILTER", lambda: draw_history(), width=14).pack(
            side="left", padx=(0, 5))
        self.btn(buttons, "RESET", lambda: reset_filters(),
                 primary=False).pack(side="left", padx=(0, 8))
        if self.is_admin_user():
            tk.Button(buttons, text="CLEAR ALL BOOKING DATA",
                      command=self.admin_clear_all_booking_data,
                      font=("Segoe UI", 9, "bold"), fg="white", bg="#DC2626",
                      activebackground="#B91C1C", activeforeground="white",
                      relief="flat", bd=0, cursor="hand2", padx=12, pady=9).pack(side="left")
        results = tk.Frame(content, bg=COLORS["bg"])
        results.pack(fill="both", expand=True)

        def reset_filters():
            start_var.set("")
            end_var.set("")
            customer_var.set("")
            status_var.set("All")
            draw_history()

        def draw_history():
            for child in results.winfo_children():
                child.destroy()
            try:
                start = self.parse_date_text(start_var.get(), "start date")
                end = self.parse_date_text(end_var.get(), "end date")
            except ValueError as error:
                messagebox.showwarning("Invalid Date Range", str(error))
                return
            if start and end and start > end:
                messagebox.showwarning("Invalid Date Range",
                                       "The start date cannot be after the end date.")
                return
            rows = self.store.reservations_history(None, start, end)
            query = customer_var.get().strip().lower()
            status = status_var.get()
            if query:
                rows = [row for row in rows if query in " ".join(
                    str(row.get(key) or "") for key in
                    ("reservation_id", "customer", "username", "service",
                     "vehicle", "booking_date")).lower()]
            if status != "All":
                rows = [row for row in rows if row.get("status") == status]
            summary = self.store.history_summary(rows)
            stats = tk.Frame(results, bg=COLORS["bg"])
            stats.pack(fill="x", pady=(0, 9))
            stat_items = [
                ("Bookings", summary["count"], COLORS["cyan"]),
                ("Completed", summary["completed"], "#36d98a"),
                ("Cancelled", summary["cancelled"], "#ff9f6b"),
                ("Rejected", summary["rejected"], "#ff6b6b"),
                ("Booking Value", money(summary["value"]), COLORS["white"]),
                ("Verified Paid", money(summary["paid"]), "#8fb8ff"),
            ]
            for index, (name, value, color) in enumerate(stat_items):
                cell = tk.Frame(stats, bg=COLORS["card"],
                                highlightbackground="#23374e", highlightthickness=1)
                cell.grid(row=index // 3, column=index % 3, sticky="nsew", padx=4, pady=4)
                stats.columnconfigure(index % 3, weight=1)
                self.label(cell, name, 8, COLORS["muted"], True).pack(
                    anchor="w", padx=10, pady=(7, 1))
                self.label(cell, str(value), 12, color, True).pack(
                    anchor="w", padx=10, pady=(0, 7))
            if not rows:
                self.empty_state(
                    results, "NO BOOKING HISTORY",
                    "No booking history found for the selected date or date range.")
                return
            table_card = self.card(results)
            table_card.pack(fill="both", expand=True)
            self.configure_admin_table_style()
            columns = ("select", "id", "customer", "service", "date", "time", "bay",
                       "total", "paid", "balance", "status", "payment")
            tree = ttk.Treeview(table_card, columns=columns, show="headings",
                                style="Admin.Treeview", selectmode="extended", height=10)
            names = {
                "select": "✓", "id": "Reservation", "customer": "Customer", "service": "Service",
                "date": "Booking Date", "time": "Time", "bay": "Bay",
                "total": "Total", "paid": "Verified Paid", "balance": "Balance",
                "status": "Booking Status", "payment": "Payment Status",
            }
            for column in columns:
                tree.heading(column, text=names[column])
                tree.column(column, width=42 if column == "select" else (110 if column == "id" else 95),
                            anchor="center", stretch=(column != "select"))
            tree.tag_configure("pending", foreground=RESERVATION_STATUS_COLORS["Pending"])
            tree.tag_configure("approved", foreground=RESERVATION_STATUS_COLORS["Approved"])
            tree.tag_configure("rejected", foreground=RESERVATION_STATUS_COLORS["Rejected"])
            tree.tag_configure("completed", foreground=RESERVATION_STATUS_COLORS["Completed"])
            tree.tag_configure("cancelled", foreground="#ff9f6b")
            tree.tag_configure("odd", background="#0b1624")
            tree.tag_configure("even", background="#101d2d")
            for index, row in enumerate(rows):
                tree.insert("", "end", iid=row["reservation_id"], values=(
                    "☐", row["reservation_id"], row["customer"], row["service"],
                    row["booking_date"], row["booking_time"], row["bay"],
                    money(row["total"]), money(row.get("verified_paid") or 0),
                    money(row.get("remaining_balance") or 0), row["status"],
                    row.get("payment_status") or "",
                ), tags=(str(row["status"]).lower(),
                         "odd" if index % 2 == 0 else "even"))
            tree.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 0))
            table_card.rowconfigure(0, weight=1)
            table_card.columnconfigure(0, weight=1)
            v_scroll = ttk.Scrollbar(table_card, orient="vertical", command=tree.yview)
            h_scroll = ttk.Scrollbar(table_card, orient="horizontal", command=tree.xview)
            v_scroll.grid(row=0, column=1, sticky="ns", pady=(8, 0))
            h_scroll.grid(row=1, column=0, sticky="ew")
            tree.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)
            tree.bind("<Double-1>", lambda _event: self._history_view_booking(tree))
            def toggle_history_checkbox(event):
                if tree.identify("column", event.x) != "#1":
                    return
                item = tree.identify_row(event.y)
                if not item:
                    return
                selected = set(tree.selection())
                if item in selected:
                    selected.remove(item)
                else:
                    selected.add(item)
                tree.selection_set(tuple(selected))
                for iid in tree.get_children():
                    values = list(tree.item(iid, "values"))
                    values[0] = "☑" if iid in selected else "☐"
                    tree.item(iid, values=values)
                return "break"
            tree.bind("<Button-1>", toggle_history_checkbox, add="+")
            actions = tk.Frame(results, bg=COLORS["bg"])
            actions.pack(fill="x", pady=(8, 0))
            self.btn(actions, "VIEW SELECTED",
                     lambda: self._history_view_booking(tree)).pack(
                         side="left", padx=(0, 6))
            self.btn(actions, "EXPORT FILTERED CSV",
                     lambda: self.export_history_csv(rows), primary=False).pack(
                         side="left", padx=(0, 6))

            def select_all_history():
                ids = tree.get_children()
                tree.selection_set(ids)
                for iid in ids:
                    values = list(tree.item(iid, "values")); values[0] = "☑"; tree.item(iid, values=values)

            def clear_history_selection():
                tree.selection_remove(tree.selection())
                for iid in tree.get_children():
                    values = list(tree.item(iid, "values")); values[0] = "☐"; tree.item(iid, values=values)

            def confirm_delete_history():
                if not self.is_admin_user():
                    messagebox.showerror("Access Denied", "Only an administrator can delete booking history records.", parent=self)
                    return
                selected_ids = list(tree.selection())
                if not selected_ids:
                    messagebox.showwarning("No Booking Selected",
                                           "Please select at least one booking record to delete.", parent=self)
                    return
                dialog = tk.Toplevel(self)
                dialog.title("Confirm Deletion")
                dialog.configure(bg=COLORS["card"])
                dialog.transient(self)
                dialog.resizable(False, False)
                dialog.grab_set()
                dialog.geometry("480x210")
                dialog.update_idletasks()
                x = self.winfo_rootx() + max(0, (self.winfo_width() - 480) // 2)
                y = self.winfo_rooty() + max(0, (self.winfo_height() - 210) // 2)
                dialog.geometry(f"480x210+{x}+{y}")
                tk.Label(dialog, text="Confirm Deletion", bg=COLORS["card"],
                         fg=COLORS["white"], font=("Segoe UI", 13, "bold")).pack(
                             anchor="w", padx=20, pady=(18, 8))
                tk.Label(dialog,
                         text="Are you sure you want to permanently delete the selected booking record(s), including their associated payment and refund history? This action cannot be undone.",
                         bg=COLORS["card"], fg=COLORS["muted"],
                         font=("Segoe UI", 10), justify="left", wraplength=400).pack(
                             anchor="w", padx=20)
                dialog_actions = tk.Frame(dialog, bg=COLORS["card"])
                dialog_actions.pack(side="bottom", fill="x", padx=20, pady=16)
                def cancel_delete():
                    dialog.grab_release()
                    dialog.destroy()
                def do_delete():
                    if getattr(self, "_booking_delete_busy", False):
                        return
                    self._booking_delete_busy = True
                    try:
                        dialog.grab_release()
                        dialog.destroy()
                        ok, message = self.store.delete_booking_history(
                            self.current_user.get("username"), selected_ids)
                        if not ok:
                            messagebox.showerror("Deletion Failed", message, parent=self)
                            return
                        draw_history()
                        messagebox.showinfo("Deletion Complete", message, parent=self)
                    finally:
                        self._booking_delete_busy = False
                tk.Button(dialog_actions, text="CANCEL", command=cancel_delete,
                          font=("Segoe UI", 9, "bold"), fg="white", bg=COLORS["button2"],
                          activebackground="#354b63", activeforeground="white",
                          relief="flat", bd=0, cursor="hand2", padx=18, pady=9).pack(
                              side="right", padx=(8, 0))
                tk.Button(dialog_actions, text="DELETE", command=do_delete,
                          font=("Segoe UI", 9, "bold"), fg="white", bg="#DC2626",
                          activebackground="#B91C1C", activeforeground="white",
                          relief="flat", bd=0, cursor="hand2", padx=18, pady=9).pack(side="right")
                dialog.protocol("WM_DELETE_WINDOW", cancel_delete)
                dialog.bind("<Escape>", lambda _event: cancel_delete())

            if self.is_admin_user():
                self.btn(actions, "SELECT ALL", select_all_history,
                         primary=False, width=11).pack(side="left", padx=(0, 6))
                self.btn(actions, "CLEAR SELECTION", clear_history_selection,
                         primary=False, width=15).pack(side="left", padx=(0, 6))
                tk.Button(actions, text="DELETE", command=confirm_delete_history,
                          font=("Segoe UI", 9, "bold"), fg="white", bg="#DC2626",
                          activebackground="#B91C1C", activeforeground="white",
                          relief="flat", bd=0, cursor="hand2", padx=16, pady=9).pack(side="left", padx=(0, 6))


        customer_entry.bind("<Return>", lambda _event: draw_history())
        status_combo.bind("<<ComboboxSelected>>", lambda _event: draw_history())
        draw_history()

    def admin_clear_all_booking_data(self):
        """Confirm and execute a full booking-data purge for an authenticated admin."""
        if not self.is_admin_user():
            messagebox.showerror("Access Denied", "Only an administrator can clear booking data.", parent=self)
            return
        if getattr(self, "_booking_delete_busy", False):
            return
        dialog = tk.Toplevel(self)
        dialog.title("Clear All Booking Data")
        dialog.configure(bg=COLORS["card"])
        dialog.transient(self)
        dialog.resizable(False, False)
        dialog.grab_set()
        dialog.geometry("540x230")
        tk.Label(dialog, text="Clear All Booking Data", bg=COLORS["card"], fg=COLORS["white"],
                 font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=20, pady=(18, 8))
        warning = ("WARNING: You are about to permanently delete ALL booking records, including associated "
                   "payment history, refund records, and booking-related notifications. This action cannot be undone. "
                   "Do you want to continue?")
        tk.Label(dialog, text=warning, bg=COLORS["card"], fg=COLORS["muted"],
                 font=("Segoe UI", 10), justify="left", wraplength=495).pack(anchor="w", padx=20)
        row = tk.Frame(dialog, bg=COLORS["card"])
        row.pack(side="bottom", fill="x", padx=20, pady=16)
        def cancel():
            try: dialog.grab_release()
            except tk.TclError: pass
            dialog.destroy()
        def confirm():
            if getattr(self, "_booking_delete_busy", False):
                return
            self._booking_delete_busy = True
            try:
                ok, msg = self.store.clear_all_booking_data(self.current_user.get("username"))
                cancel()
                if not ok:
                    messagebox.showerror("Clear Failed", msg, parent=self)
                    return
                self.show_admin_booking_history()
                messagebox.showinfo("Clear Complete", msg, parent=self)
            finally:
                self._booking_delete_busy = False
        tk.Button(row, text="CANCEL", command=cancel, font=("Segoe UI", 9, "bold"),
                  fg="white", bg=COLORS["button2"], activebackground="#354b63",
                  activeforeground="white", relief="flat", bd=0, cursor="hand2", padx=18, pady=9).pack(side="right", padx=(8, 0))
        tk.Button(row, text="CLEAR ALL DATA", command=confirm, font=("Segoe UI", 9, "bold"),
                  fg="white", bg="#DC2626", activebackground="#B91C1C",
                  activeforeground="white", relief="flat", bd=0, cursor="hand2", padx=18, pady=9).pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", cancel)
        dialog.bind("<Escape>", lambda _event: cancel())

    def export_history_csv(self, rows):
        path = filedialog.asksaveasfilename(
            parent=self, title="Export Booking History",
            defaultextension=".csv",
            initialfile=f"booking_history_{business_now().strftime('%Y%m%d_%H%M%S')}.csv",
            filetypes=[("CSV files", "*.csv")],
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["Reservation", "Customer", "Service", "Booking Date",
                                 "Time", "Bay", "Total", "Verified Paid", "Balance",
                                 "Booking Status", "Payment Status"])
                for row in rows:
                    writer.writerow([
                        row["reservation_id"], row["customer"], row["service"],
                        row["booking_date"], row["booking_time"], row["bay"],
                        f"{float(row.get('total') or 0):.2f}",
                        f"{float(row.get('verified_paid') or 0):.2f}",
                        f"{float(row.get('remaining_balance') or 0):.2f}",
                        row["status"], row.get("payment_status") or "",
                    ])
        except OSError as error:
            messagebox.showerror("Export Failed", str(error))
            return
        messagebox.showinfo("Export Complete", f"Booking history exported to:\n{path}")

    def schedule_admin_refresh(self):
        """Keep the admin table in sync so new payments appear without a manual refresh."""
        job = getattr(self, "_admin_refresh_job", None)
        if job:
            try:
                self.after_cancel(job)
            except (tk.TclError, ValueError):
                pass
        self._admin_refresh_job = self.after(5000, self.auto_refresh_admin_table)

    def auto_refresh_admin_table(self):
        """Periodically reload the admin table while the dashboard is visible."""
        self._admin_refresh_job = None
        if not getattr(self, "_admin_view_active", False):
            return
        self.refresh_admin_table()
        self.schedule_admin_refresh()

    def refresh_admin_table(self):
        if not hasattr(self, "admin_tree"):
            return
        try:
            if not self.admin_tree.winfo_exists():
                return
        except tk.TclError:
            return
        selection = self.admin_tree.selection()
        for item in self.admin_tree.get_children():
            self.admin_tree.delete(item)
        status = self.admin_filter.get() if hasattr(self, "admin_filter") else "All"
        query = self.admin_search.get().strip().lower() if hasattr(self, "admin_search") else ""
        shown = 0
        for r in self.store.all_reservations(status):
            searchable = " ".join(
                str(r[k] or "") for k in
                ["reservation_id", "customer", "contact", "username",
                 "booking_date", "service"]
            ).lower()
            if query and query not in searchable:
                continue
            status_key = str(r["status"]).strip().lower()
            payment_key = str(r["payment_status"]).strip().lower()
            if status_key == "rejected":
                row_tag = "rejected"
            elif status_key == "pending":
                row_tag = "pending"
            elif status_key == "approved":
                row_tag = "approved"
            elif status_key == "completed":
                row_tag = "completed"
            elif status_key == "refunded":
                row_tag = "refunded"
            elif status_key == "cancelled":
                row_tag = "cancelled"
            elif payment_key == "fully paid":
                row_tag = "paid"
            elif payment_key == "partially paid":
                row_tag = "partial"
            elif payment_key in ("unpaid", "pending"):
                row_tag = "unpaid"
            else:
                row_tag = ""
            zebra_tag = "odd" if shown % 2 == 0 else "even"
            tags = (row_tag, zebra_tag) if row_tag else (zebra_tag,)
            self.admin_tree.insert("", "end", iid=r["reservation_id"], values=(
                r["reservation_id"], r["customer"], r["contact"], r["vehicle"], r["service"],
                r["booking_date"], r["booking_time"], r["bay"], money(r["total"]),
                money(r["verified_paid"]), money(r["remaining_balance"]), r["status"],
                r["payment_status"]
            ), tags=tags)
            shown += 1
        if selection and self.admin_tree.exists(selection[0]):
            self.admin_tree.selection_set(selection[0])
        if hasattr(self, "admin_table_count"):
            try:
                self.admin_table_count.configure(
                    text=f"Showing {shown} reservation{'s' if shown != 1 else ''}")
            except tk.TclError:
                pass
        self.update_admin_action_state()

    def admin_set_selected(self, new_status):
        selection = self.admin_tree.selection() if hasattr(self, "admin_tree") else ()
        if not selection:
            messagebox.showinfo("Select Booking", "Select a booking first."); return
        rid = selection[0]
        r = self.store.get_reservation(rid)
        if not r:
            messagebox.showerror("Not Found", "Reservation not found."); return
        if new_status == "Rejected":
            self.admin_reject_reservation_dialog(rid)
            return
        if new_status in ("Approved",) and r["status"] not in ("Pending",):
            messagebox.showwarning("Status Unchanged", f"Only Pending bookings can be {new_status.lower()}."); return
        if new_status == "Completed" and r["status"] != "Approved":
            messagebox.showwarning("Cannot Complete", "Only an Approved booking can be marked Completed."); return
        if new_status == "Approved" and r["status"] == "Pending" and not self.store.bay_available(r["bay"], r["booking_date"], r["booking_time"], exclude_id=rid):
            messagebox.showerror("Bay Conflict", "Another pending or approved booking occupies this bay and time. Resolve the conflict before approval."); return
        if not messagebox.askyesno("Confirm Action", f"Change {rid} status from {r['status']} to {new_status}?"):
            return
        try:
            self.store.set_booking_status(rid, new_status, self.current_user["username"])
        except Exception as exc:
            messagebox.showerror("Booking Update Failed", str(exc))
            return
        messagebox.showinfo("Booking Updated", f"{rid} is now {new_status}.")
        self.show_admin_manage_bookings()

    def admin_reject_reservation_dialog(self, rid):
        """Require a reason and explicit confirmation before rejecting a pending reservation."""
        if not self.current_user or self.current_user.get("role") != "admin":
            messagebox.showerror("Access Denied", "Only an administrator can reject reservations.")
            return
        reservation = self.store.get_reservation(rid)
        if not reservation:
            messagebox.showerror("Not Found", "Reservation not found."); return
        if reservation.get("status") == "Rejected":
            messagebox.showwarning("Already Rejected", "This reservation has already been rejected."); return
        if reservation.get("status") != "Pending":
            messagebox.showwarning("Cannot Reject", "Only Pending reservations can be rejected."); return
        reasons = {
            "Equipment Malfunction": "Carwash machines or equipment are not working.",
            "Staff Unavailability": "Not enough staff are available to handle the reservation.",
            "Maintenance": "The facility or service bay is undergoing maintenance.",
            "Fully Booked": "No available time slots or service bays.",
            "Incorrect Booking Details": "The customer provided incorrect reservation information.",
            "Payment Issue": "The payment is invalid, incomplete, or cannot be verified.",
            "Service Unavailable": "The requested service is temporarily unavailable.",
            "Other": "",
        }
        dialog = tk.Toplevel(self)
        dialog.title("Reject Reservation")
        dialog.configure(bg=COLORS["card"]); dialog.transient(self); dialog.grab_set(); dialog.resizable(False, False)
        panel = self.card(dialog); panel.pack(fill="both", expand=True, padx=12, pady=12)
        self.label(panel, "REJECT RESERVATION", 14, COLORS["cyan"], True).pack(anchor="w", padx=18, pady=(14, 4))
        self.label(panel, f"Reservation: {rid}\nSelect a rejection reason. The customer will be notified.", 10, COLORS["white"], wraplength=470, justify="left").pack(anchor="w", padx=18, pady=(0, 12))
        form = tk.Frame(panel, bg=COLORS["card"]); form.pack(fill="x", padx=18)
        self.label(form, "Rejection Reason", 10, COLORS["muted"], True).pack(anchor="w", pady=(0, 4))
        reason_var = tk.StringVar(value="Equipment Malfunction")
        combo = ttk.Combobox(form, textvariable=reason_var, values=list(reasons), state="readonly", width=48)
        combo.pack(fill="x", pady=(0, 8))
        explanation = tk.StringVar(value=reasons[reason_var.get()])
        self.label(form, "Reason explanation", 9, COLORS["muted"], True).pack(anchor="w")
        tk.Label(form, textvariable=explanation, bg=COLORS["card2"], fg=COLORS["white"], anchor="w", justify="left", wraplength=460, padx=10, pady=8).pack(fill="x", pady=(3, 8))
        custom_frame = tk.Frame(form, bg=COLORS["card"])
        self.label(custom_frame, "Enter Custom Rejection Reason", 10, COLORS["muted"], True).pack(anchor="w", pady=(0, 4))
        custom_text = tk.Text(custom_frame, height=3, width=50, wrap="word", bg=COLORS["card2"], fg=COLORS["white"], insertbackground=COLORS["white"], relief="flat")
        custom_text.pack(fill="x")
        def update_reason(_event=None):
            category = reason_var.get(); explanation.set(reasons.get(category, ""))
            if category == "Other": custom_frame.pack(fill="x", pady=(0, 8))
            else: custom_frame.pack_forget()
        combo.bind("<<ComboboxSelected>>", update_reason); update_reason()
        buttons = tk.Frame(panel, bg=COLORS["card"]); buttons.pack(fill="x", padx=18, pady=(10, 16))
        def confirm():
            category = reason_var.get().strip()
            custom = custom_text.get("1.0", "end").strip() if category == "Other" else ""
            if not category or (category == "Other" and len(custom) < 5):
                messagebox.showwarning("Reason Required", "Please select a reason and enter a meaningful explanation for Other.", parent=dialog); return
            reason = custom if category == "Other" else f"{category} — {reasons[category]}"
            if not messagebox.askyesno("Confirm Rejection", f"Reject reservation {rid}?\n\nReason: {reason}\n\nThe customer will be notified.", parent=dialog): return
            try:
                self.store.set_booking_status(rid, "Rejected", self.current_user["username"], rejection_reason=reason, rejection_reason_category=category)
            except Exception as exc:
                messagebox.showerror("Rejection Failed", f"The reservation was not rejected.\n\n{exc}", parent=dialog); return
            dialog.destroy()
            messagebox.showinfo("Reservation Rejected", f"Reservation {rid} was rejected successfully. The customer has been notified.")
            self.show_admin_manage_bookings()
        reject = self.btn(buttons, "CONFIRM REJECTION", confirm, width=22)
        reject.configure(bg="#B91C1C", fg="#FFFFFF", activebackground="#991B1B", activeforeground="#FFFFFF")
        reject.pack(side="left", padx=(0, 8))
        self.btn(buttons, "GO BACK", dialog.destroy, primary=False, width=12).pack(side="left")
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.update_idletasks()
        dialog.geometry(f"+{max(0, self.winfo_rootx() + (self.winfo_width()-dialog.winfo_width())//2)}+{max(0, self.winfo_rooty() + (self.winfo_height()-dialog.winfo_height())//2)}")

    def admin_cancel_reservation_dialog(self, rid, after_cancel=None):
        """Require an administrator to select a reason before cancelling a booking."""
        if not self.current_user or self.current_user.get("role") != "admin":
            messagebox.showerror("Access Denied", "Only an administrator can cancel reservations.")
            return
        reservation = self.store.get_reservation(rid)
        if not reservation:
            messagebox.showerror("Not Found", "Reservation not found.")
            return
        if reservation.get("status") not in ("Pending", "Approved", "Cancellation Requested"):
            messagebox.showwarning("Cannot Cancel", f"Reservation status is {reservation.get('status')} and cannot be cancelled.")
            return

        reasons = {
            "Equipment Malfunction": "Carwash machines or equipment are not working.",
            "Staff Unavailability": "Not enough staff are available to handle the reservation.",
            "Maintenance": "The carwash facility is undergoing maintenance.",
            "Service Unavailable": "The selected service cannot be provided at the scheduled time.",
            "Emergency Closure": "Unexpected circumstances require the business to close.",
            "Payment Issue": "Required payment or payment confirmation was not completed, if applicable.",
            "Others": "",
        }
        dialog = tk.Toplevel(self)
        dialog.title("Cancel Reservation")
        dialog.configure(bg=COLORS["card"])
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        panel = self.card(dialog)
        panel.pack(fill="both", expand=True, padx=12, pady=12)
        self.label(panel, "CANCEL RESERVATION", 14, COLORS["cyan"], True).pack(anchor="w", padx=18, pady=(14, 4))
        self.label(panel, f"Reservation: {rid}\nPlease select the reason. The customer will be notified.", 10, COLORS["white"], wraplength=470, justify="left").pack(anchor="w", padx=18, pady=(0, 12))
        form = tk.Frame(panel, bg=COLORS["card"])
        form.pack(fill="x", padx=18)
        self.label(form, "Cancellation Reason", 10, COLORS["muted"], True).pack(anchor="w", pady=(0, 4))
        reason_var = tk.StringVar(value="Equipment Malfunction")
        combo = ttk.Combobox(form, textvariable=reason_var, values=list(reasons), state="readonly", width=48)
        combo.pack(fill="x", pady=(0, 8))
        explanation = tk.StringVar(value=reasons[reason_var.get()])
        self.label(form, "Reason explanation", 9, COLORS["muted"], True).pack(anchor="w")
        explanation_label = tk.Label(form, textvariable=explanation, bg=COLORS["card2"], fg=COLORS["white"], anchor="w", justify="left", wraplength=460, padx=10, pady=8)
        explanation_label.pack(fill="x", pady=(3, 8))
        custom_frame = tk.Frame(form, bg=COLORS["card"])
        custom_label = self.label(custom_frame, "Enter Cancellation Reason", 10, COLORS["muted"], True)
        custom_label.pack(anchor="w", pady=(0, 4))
        custom_text = tk.Text(custom_frame, height=3, width=50, wrap="word", bg=COLORS["card2"], fg=COLORS["white"], insertbackground=COLORS["white"], relief="flat")
        custom_text.pack(fill="x")

        def update_reason(_event=None):
            category = reason_var.get()
            explanation.set(reasons.get(category, ""))
            if category == "Others":
                custom_frame.pack(fill="x", pady=(0, 8))
            else:
                custom_frame.pack_forget()
        combo.bind("<<ComboboxSelected>>", update_reason)
        update_reason()
        buttons = tk.Frame(panel, bg=COLORS["card"])
        buttons.pack(fill="x", padx=18, pady=(10, 16))

        def confirm():
            category = reason_var.get().strip()
            custom = custom_text.get("1.0", "end").strip() if category == "Others" else ""
            reason = custom if category == "Others" else f"{category} – {reasons[category]}"
            if not category or (category == "Others" and len(custom) < 5):
                messagebox.showwarning("Reason Required", "Please enter a meaningful custom cancellation reason.", parent=dialog)
                return
            if not messagebox.askyesno("Confirm Cancellation", f"Permanently cancel reservation {rid}?\n\nReason: {reason}\n\nThe customer will be notified.", parent=dialog):
                return
            try:
                self.store.set_booking_status(rid, "Cancelled", self.current_user["username"], cancellation_reason=reason, cancellation_reason_category=category)
            except Exception as exc:
                messagebox.showerror("Cancellation Failed", f"The reservation was not cancelled.\n\n{exc}", parent=dialog)
                return
            dialog.destroy()
            messagebox.showinfo("Reservation Cancelled", f"Reservation {rid} was cancelled. The customer has been notified of the reason.")
            if after_cancel:
                after_cancel()
            else:
                self.show_admin_manage_bookings()
        self.btn(buttons, "CONFIRM CANCELLATION", confirm, width=23).pack(side="left", padx=(0, 8))
        self.btn(buttons, "GO BACK", dialog.destroy, primary=False, width=12).pack(side="left")
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.update_idletasks()
        dialog.geometry(f"+{max(0, self.winfo_rootx() + (self.winfo_width()-dialog.winfo_width())//2)}+{max(0, self.winfo_rooty() + (self.winfo_height()-dialog.winfo_height())//2)}")

    def admin_view_selected(self):
        selection = self.admin_tree.selection() if hasattr(self, "admin_tree") else ()
        if not selection:
            messagebox.showinfo("Select Booking", "Select a booking first."); return
        self.show_reservation_details(selection[0], customer_view=False)

    def admin_verify_payment(self, payment_id, status, rid):
        if not self.current_user or self.current_user.get("role") != "admin":
            messagebox.showerror("Access Denied", "Only an authorized admin can verify a payment.")
            return
        if status == "Verified":
            if not messagebox.askyesno("Verify Payment", "Mark this submitted payment as Verified?"):
                return
        ok, message = self.store.verify_payment(payment_id, status, self.current_user["username"])
        if not ok:
            messagebox.showerror("Payment Not Updated", message)
            return
        messagebox.showinfo("Payment Updated", message)
        self.show_reservation_details(rid, customer_view=False)

    def admin_approve_payment(self, payment_id, rid):
        """Approve a verified payment and move the booking along the approval workflow."""
        if not self.current_user or self.current_user.get("role") != "admin":
            messagebox.showerror("Access Denied", "Only an authorized admin can approve a payment.")
            return
        ok, message = self.store.approve_payment(payment_id, self.current_user["username"])
        if not ok:
            messagebox.showerror("Payment Not Approved", message)
            return
        reservation = self.store.get_reservation(rid)
        note = ""
        if reservation and reservation["status"] == "Pending":
            self.store.set_booking_status(rid, "Approved", self.current_user["username"])
            note = f"\n\nReservation {rid} is now Approved."
        elif reservation:
            note = f"\n\nReservation {rid} stays {reservation['status']}."
        messagebox.showinfo("Payment Approved", message + note)
        self.show_reservation_details(rid, customer_view=False)

    def show_payment_image(self, payment):
        """Open a clear, full-size preview of a submitted payment reference image."""
        path = self.store.payment_image_path(payment)
        if not path:
            messagebox.showwarning(
                "Image Not Available",
                "No payment reference image is stored for this payment record."
            )
            return
        dialog = tk.Toplevel(self)
        dialog.title("Payment Reference Image")
        dialog.configure(bg=COLORS["bg"])
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        panel = self.card(dialog)
        panel.pack(fill="both", expand=True, padx=14, pady=14)
        self.label(panel, "PAYMENT REFERENCE IMAGE", 13, COLORS["cyan"], True).pack(
            anchor="w", padx=18, pady=(14, 4))
        details = (f"Payment: PAY-{int(payment.get('payment_id', 0)):03d}  •  "
                   f"Reservation: {payment.get('reservation_id', 'N/A')}  •  "
                   f"File: {payment.get('reference_image_name') or path.name}")
        self.label(panel, details, 9, COLORS["muted"]).pack(anchor="w", padx=18, pady=(0, 8))
        max_width = max(320, min(820, self.winfo_screenwidth() - 160))
        max_height = max(240, min(560, self.winfo_screenheight() - 220))
        photo = load_payment_image(path, max_width=max_width, max_height=max_height)
        if photo is not None:
            image_label = tk.Label(panel, image=photo, bg=COLORS["card2"],
                                   highlightbackground="#23374e", highlightthickness=1)
            image_label.image = photo
            image_label.pack(padx=18, pady=(0, 10))
        else:
            self.label(panel, f"The image could not be displayed.\nFile: {path}",
                       10, COLORS["muted"], justify="left").pack(anchor="w", padx=18, pady=(0, 10))
        controls = tk.Frame(panel, bg=COLORS["card"])
        controls.pack(fill="x", padx=18, pady=(0, 14))
        self.btn(controls, "OPEN IMAGE FILE",
                 lambda p=path: self.open_payment_image_file(p), width=17).pack(side="left", padx=(0, 6))
        self.btn(controls, "CLOSE", dialog.destroy, primary=False, width=10).pack(side="left")

    def open_payment_image_file(self, path):
        """Open the stored image with the operating system's default image viewer."""
        path = Path(path)
        if not path.is_file():
            messagebox.showerror("Image Not Found", "The payment reference image file could not be found.")
            return
        try:
            os.startfile(str(path))
        except (AttributeError, OSError) as exc:
            messagebox.showerror(
                "Cannot Open Image",
                f"The image could not be opened.\n\n{exc}\n\nImage location:\n{path}"
            )
    
    def admin_confirm_cancellation_request(self, rid):
        """Confirm a customer cancellation request after collecting the admin's reason."""
        if not self.current_user or self.current_user.get("role") != "admin":
            messagebox.showerror("Access Denied", "Administrator access is required.")
            return
        r = self.store.get_reservation(rid)
        if not r:
            messagebox.showerror("Not Found", "Reservation not found.")
            return
        if r.get("status") != "Cancellation Requested":
            messagebox.showwarning("Invalid Status", "Only reservations with Cancellation Requested status can be confirmed.")
            return
        confirmed_paid = round(self.store._verified_total(rid), 2)
        pending_paid = round(self.store._pending_total(rid), 2)
        refund_request = self.store.refund_for_reservation(rid)
        if pending_paid > 0.005 and confirmed_paid <= 0.005:
            if not messagebox.askyesno("Unverified Payment", f"Reservation {rid} has {money(pending_paid)} in unverified submissions but no verified payment. Cancel without creating a refund request?"):
                return
        def after_cancel():
            if refund_request and refund_request.get("status") != "Completed":
                messagebox.showinfo("Cancellation Confirmed", "Cancellation confirmed. Opening the customer's refund request for admin review.")
                self.show_admin_refunds(reservation_id=rid)
            elif confirmed_paid > 0.005:
                messagebox.showinfo("Cancellation Confirmed", "Cancellation confirmed. No refund record was created because the customer has not submitted a refund request. The customer can still request a refund from Reservation Details.")
                self.show_admin_manage_bookings()
            else:
                self.show_admin_manage_bookings()
        self.admin_cancel_reservation_dialog(rid, after_cancel=after_cancel)

    def generate_receipt(self, rid):
        r = self.store.get_reservation(rid)
        if not r:
            messagebox.showerror("Not Found", "Reservation not found."); return
        if self.current_user["role"] == "customer" and r["username"] != self.current_user["username"]:
            messagebox.showerror("Access Denied", "You can only generate receipts for your own bookings."); return
        payments = self.store.payments_for_reservation(rid)
        verified = sum(float(p["amount_submitted"]) for p in payments if p["verification_status"] == "Verified")
        latest = payments[0] if payments else None
        balance = max(0, float(r["total"]) - verified)
        status_label, confirmed, pending, remaining = self.store.payment_status(rid)
        reference_image_text = "N/A"
        if latest and latest.get("reference_image"):
            reference_image_text = latest.get("reference_image_name") or Path(latest["reference_image"]).name
        receipt_no = f"RCPT-{rid}-{business_now().strftime('%Y%m%d%H%M%S')}"
        lines = [
            "WASHAPP - SMART CARWASH RESERVATION SYSTEM",
            "RECEIPT - NOT A TAX INVOICE",
            "=" * 48, f"Receipt No.: {receipt_no}", f"Receipt Date: {business_now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Reservation ID: {rid}", f"Customer: {r['customer']}", f"Contact: {r['contact']}",
            f"Vehicle: {r['vehicle']}", f"Service: {r['service']}", f"Date: {r['booking_date']}",
            f"Time: {r['booking_time']}", f"Bay: {r['bay']}", f"Booking Status: {r['status']}",
            "-" * 48, f"Total Service Price: {money(r['total'])}",
            f"Required 30% Downpayment: {money(round(float(r['total'])*DOWNPAYMENT_RATE,2))}",
            f"Verified Amount Paid: {money(verified)}", f"Remaining Balance: {money(balance)}",
            f"Payment Method: {latest['method'] if latest else 'Not submitted'}",
            f"Payment Reference: {latest['reference'] if latest else 'N/A'}",
            f"Payment Reference Image: {reference_image_text}",
            f"Payment Date: {format_timestamp(latest['submitted_at']) if latest else 'N/A'}",
            f"Payment Status: {status_label}",
            f"Payment Approval: {latest.get('approval_status') if latest and latest.get('approval_status') else 'Not approved yet'}",
            "-" * 48, "PAYMENT HISTORY"
        ]
        if payments:
            for record in payments:
                lines.append(
                    f"{record['payment_id']:03d} | {format_timestamp(record.get('submitted_at'))} | "
                    f"{record.get('method')} | {money(record.get('amount_submitted') or 0)} | "
                    f"Ref: {record.get('reference')} | {record.get('verification_status')}"
                )
        else:
            lines.append("No payment recorded for this reservation.")
        lines.append("=" * 48)
        path = RECEIPT_DIR / f"{receipt_no}.txt"
        path.write_text("\n".join(lines), encoding="utf-8")
        messagebox.showinfo("Receipt Generated", f"Receipt saved to:\n{path}")
        try:
            os.startfile(path)
        except (AttributeError, OSError):
            pass

    def logout(self):
        if messagebox.askyesno("Logout", "Are you sure you want to log out?"):
            self.current_user = None
            self.show_landing()

    def on_close(self):
        try:
            self.store.close()
        finally:
            self.destroy()


if __name__ == "__main__":
    app = WashApp()
    app.mainloop()
