import csv
from datetime import datetime
from decimal import Decimal
from typing import Optional

from django.contrib.auth import get_user_model
from django.contrib.gis.geos import GEOSGeometry
from django.core.management.base import BaseCommand, CommandError

from farms.models import Farm, Plot, CropType
from users.models import Role


class Command(BaseCommand):
    """
    Import ~700 sugarcane farmers/farms from a CSV file into existing schema.

    This command is designed to be **safe and explicit**:
    - It does NOT change any schema.
    - It can run in --dry-run mode to only print what it would do.
    - It matches records by primary keys / stable identifiers, not by guessing.

    Expected CSV columns (you can adapt them in the code if your headers differ):
      - farm_id           (optional; if present, we update an existing Farm)
      - plot_id           (optional; if present, we update an existing Plot)
      - username          (farmer username; we look up existing user)
      - email             (optional; used only if you adapt logic to create users)
      - gat_number
      - plot_number
      - village
      - taluka
      - district
      - area_size         (in acres, numeric)
      - plantation_date   (YYYY-MM-DD or ISO)
      - boundary_hex      (hex-encoded WKB / HEXEWKB polygon, SRID 4326)

    All 700 entries are assumed to be sugarcane; we will attach them
    to a `CropType` called "sugarcane" (creating it per-industry if needed).
    """

    help = "Import sugarcane farms (farmer + plot + farm + boundary) from a CSV file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            "-f",
            dest="file_path",
            required=True,
            help="Path to the CSV file exported from your Excel.",
        )
        parser.add_argument(
            "--industry-id",
            dest="industry_id",
            type=int,
            required=True,
            help="ID of the Industry all these farms belong to.",
        )
        parser.add_argument(
            "--dry-run",
            dest="dry_run",
            action="store_true",
            help="Print actions without writing to the database.",
        )
        parser.add_argument(
            "--create-users",
            dest="create_users",
            action="store_true",
            help="Create farmer users from CSV when they do not already exist.",
        )

    def handle(self, *args, **options):
        file_path = options["file_path"]
        industry_id = options["industry_id"]
        dry_run: bool = options["dry_run"]
        create_users: bool = options["create_users"]

        User = get_user_model()

        try:
            f = open(file_path, newline="", encoding="utf-8")
        except FileNotFoundError as exc:
            raise CommandError(f"CSV file not found: {file_path}") from exc

        self.stdout.write(self.style.NOTICE(f"Reading CSV: {file_path}"))

        created_farms = 0
        updated_farms = 0
        created_plots = 0
        updated_plots = 0
        skipped_rows = 0

        with f:
            reader = csv.DictReader(f)
            # Normalize headers once: "Username" -> "username", "Gat number" -> "gat_number", etc.
            normalized_headers = {
                (h or "").strip().lower().replace(" ", "_") for h in (reader.fieldnames or [])
            }
            # We need either username or phone_number, plus basic plot keys
            required_cols = {"gat_number", "village", "district"}
            missing = required_cols - normalized_headers
            if missing:
                raise CommandError(
                    f"CSV is missing required columns (after normalization): "
                    f"{', '.join(sorted(missing))}"
                )

            for row in reader:
                # Normalize this row's keys similarly
                norm = {
                    (k or "").strip().lower().replace(" ", "_"): (v or "").strip()
                    for k, v in row.items()
                }
                username_raw = norm.get("username", "")
                phone_raw = norm.get("phone_number", "")

                if not username_raw and not phone_raw:
                    skipped_rows += 1
                    continue

                # Prefer matching by phone number if present
                user = None
                if phone_raw:
                    user = User.objects.filter(phone_number=phone_raw).first()
                if user is None and username_raw:
                    user = User.objects.filter(username=username_raw).first()

                created_user = False
                if user is None:
                    if not create_users:
                        self.stdout.write(
                            self.style.WARNING(
                                f"Skipping row for username '{username_raw}' / "
                                f"phone '{phone_raw}': user not found."
                            )
                        )
                        skipped_rows += 1
                        continue

                    # Create a new Farmer user
                    farmer_role = Role.objects.filter(name__iexact="farmer").first()
                    username_for_new = phone_raw or username_raw.replace(" ", "_")
                    if not username_for_new:
                        self.stdout.write(
                            self.style.WARNING(
                                "Skipping row with no usable username/phone to create user."
                            )
                        )
                        skipped_rows += 1
                        continue

                    user = User(username=username_for_new)
                    created_user = True

                # For both new and existing users, overwrite profile data from CSV
                if phone_raw:
                    user.phone_number = phone_raw
                email_val = norm.get("email_address", "") or norm.get("email", "")
                if email_val:
                    user.email = email_val
                first_name_val = norm.get("first_name", "")
                if first_name_val:
                    user.first_name = first_name_val
                last_name_val = norm.get("last_name", "")
                if last_name_val:
                    user.last_name = last_name_val
                addr_val = norm.get("address", "")
                if addr_val:
                    user.address = addr_val
                vill_val = norm.get("village", "")
                if vill_val:
                    user.village = vill_val
                taluka_val = norm.get("taluka", "")
                if taluka_val:
                    user.taluka = taluka_val
                state_val = norm.get("state", "")
                if state_val:
                    user.state = state_val
                district_val = norm.get("district", "")
                if district_val:
                    user.district = district_val
                user.industry_id = industry_id

                farmer_role = Role.objects.filter(name__iexact="farmer").first()
                if farmer_role:
                    user.role = farmer_role

                # Only set/override password when we are creating the user
                if created_user:
                    password_raw = norm.get("password", "") or "farm@123"
                    user.set_password(password_raw)

                if dry_run:
                    self.stdout.write(
                        self.style.NOTICE(
                            f"[DRY-RUN] Would {'create' if created_user else 'update'} "
                            f"User '{user.username}' (phone={user.phone_number})."
                        )
                    )
                else:
                    user.save()
                    if created_user:
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"Created farmer user '{user.username}' "
                                f"(phone={user.phone_number})."
                            )
                        )
                    else:
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"Updated farmer user '{user.username}' "
                                f"(phone={user.phone_number})."
                            )
                        )

                # Resolve / create (or fully replace) Plot
                gat_number = norm.get("gat_number", "")
                plot_number = norm.get("plot_number", "")
                village = norm.get("village", "")
                taluka = norm.get("taluka", "")
                district = norm.get("district", "")
                state = norm.get("state", "")
                pin_code = norm.get("pin_code", "")

                plot_id_raw = norm.get("plot_id", "")
                plot: Optional[Plot] = None

                if plot_id_raw:
                    try:
                        plot = Plot.objects.get(pk=int(plot_id_raw))
                    except (ValueError, Plot.DoesNotExist):
                        plot = None

                if plot is None:
                    # match by unique-together key if possible
                    try:
                        plot = Plot.objects.get(
                            gat_number=gat_number,
                            plot_number=plot_number,
                            village=village,
                            taluka=taluka,
                            district=district,
                        )
                        action = "replace"  # user requested to replace old plot with new data
                    except Plot.DoesNotExist:
                        plot = Plot()
                        action = "create"
                else:
                    action = "replace"

                # In both create and replace, overwrite plot fields from CSV
                plot.gat_number = gat_number
                plot.plot_number = plot_number
                plot.village = village
                plot.taluka = taluka
                plot.district = district
                if state:
                    plot.state = state
                if pin_code:
                    plot.pin_code = pin_code
                plot.industry_id = industry_id
                plot.farmer = user
                plot.created_by = user

                # geometry: boundary hex -> Polygon
                boundary_hex = norm.get("boundary", "") or norm.get("boundary_hex", "")
                if boundary_hex:
                    try:
                        # GEOSGeometry accepts HEXEWKB strings directly (SRID 4326 for lat/long).
                        plot.boundary = GEOSGeometry(boundary_hex, srid=4326)
                    except Exception as ge_exc:  # noqa: BLE001
                        self.stdout.write(
                            self.style.WARNING(
                                f"Plot boundary decode failed for user {username_raw}, "
                                f"gat {gat_number}, plot {plot_number}: {ge_exc}"
                            )
                        )

                # geometry: location hex -> Point (optional)
                location_hex = norm.get("location", "")
                if location_hex:
                    try:
                        plot.location = GEOSGeometry(location_hex, srid=4326)
                    except Exception as ge_exc:  # noqa: BLE001
                        self.stdout.write(
                            self.style.WARNING(
                                f"Plot location decode failed for user {username_raw}, "
                                f"gat {gat_number}, plot {plot_number}: {ge_exc}"
                            )
                        )

                if dry_run:
                    self.stdout.write(
                        self.style.NOTICE(
                            f"[DRY-RUN] Would {action} Plot for user {user.username} "
                            f"(gat={gat_number}, plot={plot_number}, village={village})"
                        )
                    )
                else:
                    plot.save()
                    if action == "create":
                        created_plots += 1
                    else:
                        updated_plots += 1

                # Get or create a sugarcane CropType with this row's plantation_type and planting_method
                plantation_type_val = norm.get("plantation_type", "") or ""
                planting_method_val = norm.get("planting_method", "") or ""
                crop_type = CropType.objects.filter(
                    industry_id=industry_id,
                    crop_type__iexact="sugarcane",
                    plantation_type=plantation_type_val,
                    planting_method=planting_method_val,
                ).first()
                if crop_type is None and not dry_run:
                    crop_type = CropType.objects.create(
                        industry_id=industry_id,
                        crop_type="sugarcane",
                        plantation_type=plantation_type_val,
                        planting_method=planting_method_val,
                    )
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Created sugarcane CropType (plantation_type={plantation_type_val}, "
                            f"planting_method={planting_method_val}) for industry {industry_id}."
                        )
                    )
                elif crop_type is None and dry_run:
                    # In dry-run we don't create CropType; use any sugarcane CropType for display
                    crop_type = CropType.objects.filter(
                        industry_id=industry_id, crop_type__iexact="sugarcane"
                    ).first()

                # Resolve / create Farm
                farm_id_raw = norm.get("farm_id", "")
                farm: Optional[Farm] = None
                if farm_id_raw:
                    try:
                        farm = Farm.objects.get(pk=int(farm_id_raw))
                    except (ValueError, Farm.DoesNotExist):
                        farm = None

                if farm is None:
                    farm = Farm(
                        farm_owner=user,
                        created_by=user,
                        plot=plot,
                        industry_id=industry_id,
                        soil_type_id=None,
                        crop_type=crop_type,
                        address=norm.get("address", "") or "",
                    )
                    farm_action = "create"
                else:
                    farm_action = "update"

                # address (required by model)
                if norm.get("address", ""):
                    farm.address = norm.get("address", "")

                # area_size (required by DB)
                # 1. Prefer explicit CSV value if provided
                area_size_raw = norm.get("area_size", "")
                if area_size_raw:
                    try:
                        farm.area_size = Decimal(area_size_raw)
                    except (ValueError, Exception):
                        farm.area_size = None
                        self.stdout.write(
                            self.style.WARNING(
                                f"Invalid area_size '{area_size_raw}' for user {user.username}; "
                                f"will attempt to compute from boundary."
                            )
                        )
                else:
                    farm.area_size = None

                # 2. If still missing, try to compute from plot boundary (in acres)
                if farm.area_size is None and plot and plot.boundary:
                    try:
                        geom = plot.boundary.clone()
                        # Transform from WGS84 (4326) to Web Mercator (3857) for area in m²
                        if geom.srid != 3857:
                            geom.transform(3857)
                        area_m2 = Decimal(str(geom.area))
                        farm.area_size = (area_m2 / Decimal("4046.8564224")).quantize(
                            Decimal("0.0001")
                        )
                    except Exception:
                        self.stdout.write(
                            self.style.WARNING(
                                f"Could not compute area_size from boundary for user {user.username}; "
                                f"defaulting to 0."
                            )
                        )
                        farm.area_size = Decimal("0")

                # 3. Absolute fallback: never leave it null
                if farm.area_size is None:
                    farm.area_size = Decimal("0")

                # plantation_date on Farm
                plantation_date_str = norm.get("plantation_date", "")
                if plantation_date_str:
                    farm.plantation_date = self._parse_date_safe(
                        plantation_date_str, username=user.username
                    )

                if dry_run:
                    self.stdout.write(
                        self.style.NOTICE(
                            f"[DRY-RUN] Would {farm_action} Farm for user {user.username} "
                            f"(plot_id={plot.id if plot else 'None'}, area_size={farm.area_size}, "
                            f"plantation_date={farm.plantation_date}, "
                            f"plantation_type={plantation_type_val or 'None'}, "
                            f"planting_method={planting_method_val or 'None'})"
                        )
                    )
                else:
                    farm.save()
                    if farm_action == "create":
                        created_farms += 1
                    else:
                        updated_farms += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Created {created_plots} plots, updated {updated_plots} plots; "
                f"created {created_farms} farms, updated {updated_farms} farms; "
                f"skipped {skipped_rows} rows."
            )
        )

    @staticmethod
    def _parse_date_safe(value: str, *, username: str = ""):
        """
        Try multiple formats for plantation_date. Returns a date or None.
        CSV often has DD-MM-YYYY (e.g. 15-02-2025).
        """
        value = (value or "").strip()
        if not value:
            return None
        # Date-only formats (use first 10 chars)
        for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(value[:10], fmt).date()
            except ValueError:
                continue
        # ISO with time (use first 19 chars)
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ"):
            try:
                return datetime.strptime(value[:19], fmt).date()
            except ValueError:
                continue
        return None

