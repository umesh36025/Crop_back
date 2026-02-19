import json
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from farms.models import Farm, CropType


class Command(BaseCommand):
    help = (
        "Restore plantation_date, plantation_type and planting_method for farms "
        "from a JSON export file (e.g. farms_plantation_export.json)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            "-f",
            dest="file_path",
            default="farms_plantation_export.json",
            help="Path to the JSON export file (default: farms_plantation_export.json)",
        )

    def handle(self, *args, **options):
        file_path = options["file_path"]

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError as exc:
            raise CommandError(f"JSON file not found: {file_path}") from exc
        except json.JSONDecodeError as exc:
            raise CommandError(f"Invalid JSON in file {file_path}: {exc}") from exc

        if not isinstance(data, list):
            raise CommandError("Expected JSON file to contain a list of records.")

        self.stdout.write(self.style.NOTICE(f"Loading data from JSON file: {file_path}"))

        farm_updates = 0
        croptype_updates = 0
        skipped = 0

        for entry in data:
            model_label = entry.get("model")
            pk = entry.get("pk")
            fields = entry.get("fields", {})

            # Restore Farm.plantation_date
            if model_label == "farms.farm":
                if pk is None:
                    skipped += 1
                    continue

                try:
                    farm = Farm.objects.select_related("farm_owner", "crop_type").get(pk=pk)
                except Farm.DoesNotExist:
                    skipped += 1
                    continue

                username = getattr(farm.farm_owner, "username", None) or "unknown"

                plantation_date_str = fields.get("plantation_date")
                plantation_date = None
                if plantation_date_str:
                    # Accept 'YYYY-MM-DD' or ISO8601 with time
                    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ"):
                        try:
                            plantation_date = datetime.strptime(
                                plantation_date_str[:19], fmt
                            ).date()
                            break
                        except ValueError:
                            continue

                farm.plantation_date = plantation_date
                farm.save(update_fields=["plantation_date"])
                farm_updates += 1

                self.stdout.write(
                    self.style.SUCCESS(
                        f"✓ Updated farm {farm.id} for user {username} "
                        f"(plantation_date={plantation_date or 'None'})"
                    )
                )

            # Restore CropType.plantation_type & planting_method
            elif model_label == "farms.croptype":
                if pk is None:
                    skipped += 1
                    continue

                plantation_type = fields.get("plantation_type")
                planting_method = fields.get("planting_method")

                if not plantation_type and not planting_method:
                    skipped += 1
                    continue

                try:
                    croptype = CropType.objects.get(pk=pk)
                except CropType.DoesNotExist:
                    skipped += 1
                    continue

                if plantation_type:
                    croptype.plantation_type = plantation_type
                if planting_method:
                    croptype.planting_method = planting_method
                croptype.save(update_fields=["plantation_type", "planting_method"])
                croptype_updates += 1

                self.stdout.write(
                    self.style.SUCCESS(
                        f"✓ Updated CropType {croptype.id} "
                        f"(plantation_type={plantation_type or 'None'}, "
                        f"planting_method={planting_method or 'None'})"
                    )
                )

            else:
                # Not a farms.farm or farms.croptype record; ignore safely
                skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Updated {farm_updates} farms and {croptype_updates} crop types, "
                f"skipped {skipped} unrelated or invalid records."
            )
        )

