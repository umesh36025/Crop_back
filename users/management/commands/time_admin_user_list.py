"""
Management command to measure User admin list performance.
Run: python manage.py time_admin_user_list

Use this to verify that select_related reduces query count and time.
"""
import time
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import connection, reset_queries
from django.conf import settings

User = get_user_model()


class Command(BaseCommand):
    help = "Measure User admin list performance (simulates /admin/users/user/ list view)"

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=100,
            help='Number of users to simulate (default: 100)',
        )
        parser.add_argument(
            '--debug',
            action='store_true',
            help='Enable query logging (requires DEBUG=True)',
        )

    def handle(self, *args, **options):
        limit = options['limit']
        debug = options['debug']

        self.stdout.write(f"Simulating User admin list for {limit} users...\n")

        # Test 1: Without select_related (N+1 queries)
        reset_queries()
        start = time.perf_counter()
        users = list(User.objects.all()[:limit])
        for u in users:
            _ = u.role
            _ = u.industry
            _ = u.created_by.email if u.created_by else "N/A"
        elapsed = time.perf_counter() - start
        queries_without = len(connection.queries) if settings.DEBUG else "N/A"

        self.stdout.write(self.style.WARNING(
            f"Without select_related: {queries_without} queries, {elapsed:.3f}s"
        ))

        # Test 2: With select_related (optimized)
        reset_queries()
        start = time.perf_counter()
        users = list(User.objects.select_related('role', 'industry', 'created_by')[:limit])
        for u in users:
            _ = u.role
            _ = u.industry
            _ = u.created_by.email if u.created_by else "N/A"
        elapsed = time.perf_counter() - start
        queries_with = len(connection.queries) if settings.DEBUG else "N/A"

        self.stdout.write(self.style.SUCCESS(
            f"With select_related:    {queries_with} queries, {elapsed:.3f}s"
        ))

        self.stdout.write("")
        if settings.DEBUG:
            self.stdout.write(f"Query reduction: {queries_without - queries_with} fewer queries")
        else:
            self.stdout.write("(Set DEBUG=True to see query counts)")
        self.stdout.write(f"Cache backend: {settings.CACHES['default']['BACKEND'].split('.')[-1]}")
        self.stdout.write(f"Session engine: {getattr(settings, 'SESSION_ENGINE', 'db').split('.')[-1]}")

        if debug and connection.queries:
            self.stdout.write("\n--- Last 5 queries ---")
            for q in connection.queries[-5:]:
                self.stdout.write(f"  {q['sql'][:80]}...")
