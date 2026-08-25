"""``TeamService`` against the real schema — SPEC-005 §4.2.

The roster existed as a table and a read path long before anything could write to it: rows
were created only at signup (one, for yourself) and by the seed script. So its actual
purpose — turning "send it to Priya" into an address an action can execute against — was
unreachable for every name a meeting mentioned. These tests cover the write path that fixes
that, and they lean on the properties that make the roster safe to expose: per-user
isolation, and an address that cannot pass validation here only to be rejected at send.
"""

from __future__ import annotations

import pytest

from app.core.exceptions import AppError
from app.services.team import TeamService


def _service(db_session) -> TeamService:
    return TeamService(db_session)


class TestAdding:
    async def test_a_member_is_added_and_returned(self, make_user, db_session):
        user = await make_user()

        member = await _service(db_session).add(
            user.id, name="Anjali Pandey", email="anjali@acme.test",
            role="Product", request_id="r1",
        )

        assert member["name"] == "Anjali Pandey"
        assert member["email"] == "anjali@acme.test"
        assert member["role"] == "Product"
        assert member["id"]

    async def test_the_address_is_stored_lowercased(self, make_user, db_session):
        """`_resolve_email` and `VAL_OWNER_KNOWN`'s successor both compare addresses
        case-insensitively, and every provider treats them that way. Normalising on write
        means the stored value is the comparable one."""
        user = await make_user()

        member = await _service(db_session).add(
            user.id, name="Anjali", email="  Anjali@ACME.test  ", role=None, request_id="r1"
        )

        assert member["email"] == "anjali@acme.test"

    async def test_an_empty_role_is_stored_as_null_not_an_empty_string(
        self, make_user, db_session
    ):
        user = await make_user()

        member = await _service(db_session).add(
            user.id, name="Anjali", email="anjali@acme.test", role="   ", request_id="r1"
        )

        assert member["role"] is None

    async def test_a_malformed_address_is_refused_with_the_field_named(
        self, make_user, db_session
    ):
        """Named field, so the form can highlight the input rather than showing a banner."""
        user = await make_user()

        with pytest.raises(AppError) as excinfo:
            await _service(db_session).add(
                user.id, name="Anjali", email="not-an-email", role=None, request_id="r1"
            )

        assert excinfo.value.code == "invalid_email"
        assert excinfo.value.details.get("field") == "email"

    async def test_the_same_address_twice_is_refused(self, make_user, db_session):
        """Checked before the insert, not caught after: a unique-violation would already
        have poisoned the transaction, and "you already have them" is the useful answer."""
        user = await make_user()
        service = _service(db_session)
        await service.add(
            user.id, name="Anjali", email="anjali@acme.test", role=None, request_id="r1"
        )

        with pytest.raises(AppError) as excinfo:
            await service.add(
                user.id, name="Anjali Again", email="ANJALI@acme.test", role=None,
                request_id="r2",
            )

        assert excinfo.value.code == "duplicate_email"

    async def test_two_users_may_each_have_the_same_person(
        self, make_user, db_session
    ):
        """A roster is per-user, not an org directory. The unique index is on
        `(userId, email)`, and treating it as global would make one user's address book
        block another's."""
        one = await make_user(email="one@acme.test")
        two = await make_user(email="two@acme.test")
        service = _service(db_session)

        await service.add(
            one.id, name="Anjali", email="anjali@acme.test", role=None, request_id="r1"
        )
        member = await service.add(
            two.id, name="Anjali", email="anjali@acme.test", role=None, request_id="r2"
        )

        assert member["email"] == "anjali@acme.test"


class TestListing:
    async def test_members_come_back_sorted_by_name_case_insensitively(
        self, make_user, db_session
    ):
        """Read as an address book, so alphabetical — and a lowercase name must not sort
        after every capitalised one, which a naive ORDER BY would do."""
        user = await make_user()
        service = _service(db_session)
        for name, email in [("zara", "z@acme.test"), ("Anjali", "a@acme.test"), ("mike", "m@acme.test")]:
            await service.add(user.id, name=name, email=email, role=None, request_id="r")

        listed = await service.list_members(user.id)

        assert [m["name"] for m in listed["members"]] == ["Anjali", "mike", "zara"]

    async def test_one_users_roster_never_includes_anothers(self, make_user, db_session):
        one = await make_user(email="one@acme.test")
        two = await make_user(email="two@acme.test")
        service = _service(db_session)
        await service.add(one.id, name="Mine", email="mine@acme.test", role=None, request_id="r")
        await service.add(two.id, name="Theirs", email="theirs@acme.test", role=None, request_id="r")

        listed = await service.list_members(one.id)

        assert [m["name"] for m in listed["members"]] == ["Mine"]


class TestUpdating:
    async def test_a_single_field_can_be_changed_alone(self, make_user, db_session):
        user = await make_user()
        service = _service(db_session)
        member = await service.add(
            user.id, name="Anjali", email="anjali@acme.test", role="Product", request_id="r"
        )

        updated = await service.update(user.id, member["id"], name="Anjali Pandey")

        assert updated["name"] == "Anjali Pandey"
        assert updated["email"] == "anjali@acme.test"
        assert updated["role"] == "Product", "an untouched field must not be cleared"

    async def test_a_role_is_cleared_only_when_explicitly_provided_as_null(
        self, make_user, db_session
    ):
        """"Leave the role alone" and "clear the role" are different requests, and a bare
        `None` cannot tell them apart — hence `role_provided`."""
        user = await make_user()
        service = _service(db_session)
        member = await service.add(
            user.id, name="Anjali", email="anjali@acme.test", role="Product", request_id="r"
        )

        untouched = await service.update(user.id, member["id"], name="Anjali P")
        assert untouched["role"] == "Product"

        cleared = await service.update(
            user.id, member["id"], role=None, role_provided=True
        )
        assert cleared["role"] is None

    async def test_moving_to_an_address_someone_else_holds_is_refused(
        self, make_user, db_session
    ):
        user = await make_user()
        service = _service(db_session)
        await service.add(user.id, name="A", email="a@acme.test", role=None, request_id="r")
        b = await service.add(user.id, name="B", email="b@acme.test", role=None, request_id="r")

        with pytest.raises(AppError) as excinfo:
            await service.update(user.id, b["id"], email="a@acme.test")

        assert excinfo.value.code == "duplicate_email"

    async def test_keeping_your_own_address_is_not_a_duplicate(self, make_user, db_session):
        """The self-collision trap: an update that re-sends the unchanged address must not
        trip the duplicate check against the row being edited."""
        user = await make_user()
        service = _service(db_session)
        member = await service.add(
            user.id, name="Anjali", email="anjali@acme.test", role=None, request_id="r"
        )

        updated = await service.update(
            user.id, member["id"], name="Anjali P", email="anjali@acme.test"
        )

        assert updated["email"] == "anjali@acme.test"

    async def test_another_users_member_is_not_found(self, make_user, db_session):
        """Ownership is enforced in the query, so a guessed id reads as absent rather than
        forbidden — and cannot be edited across accounts."""
        one = await make_user(email="one@acme.test")
        two = await make_user(email="two@acme.test")
        service = _service(db_session)
        theirs = await service.add(
            two.id, name="Theirs", email="theirs@acme.test", role=None, request_id="r"
        )

        with pytest.raises(AppError) as excinfo:
            await service.update(one.id, theirs["id"], name="Hijacked")

        assert excinfo.value.status_code == 404


class TestRemoving:
    async def test_a_member_is_removed(self, make_user, db_session):
        user = await make_user()
        service = _service(db_session)
        member = await service.add(
            user.id, name="Anjali", email="anjali@acme.test", role=None, request_id="r"
        )

        assert await service.remove(user.id, member["id"], "r") == {"ok": True}
        assert (await service.list_members(user.id))["members"] == []

    async def test_removing_twice_is_a_404_rather_than_a_crash(self, make_user, db_session):
        """A double-clicked delete button must not produce a 500."""
        user = await make_user()
        service = _service(db_session)
        member = await service.add(
            user.id, name="Anjali", email="anjali@acme.test", role=None, request_id="r"
        )
        await service.remove(user.id, member["id"], "r")

        with pytest.raises(AppError) as excinfo:
            await service.remove(user.id, member["id"], "r")

        assert excinfo.value.status_code == 404

    async def test_another_users_member_cannot_be_removed(self, make_user, db_session):
        one = await make_user(email="one@acme.test")
        two = await make_user(email="two@acme.test")
        service = _service(db_session)
        theirs = await service.add(
            two.id, name="Theirs", email="theirs@acme.test", role=None, request_id="r"
        )

        with pytest.raises(AppError) as excinfo:
            await service.remove(one.id, theirs["id"], "r")

        assert excinfo.value.status_code == 404
        assert len((await service.list_members(two.id))["members"]) == 1
