import pytest

from pip_agent.todo import TodoManager


class TestTodoManagerWrite:
    def test_write_creates_items(self):
        mgr = TodoManager()
        mgr.write([
            {"id": "a", "content": "task A", "status": "pending"},
            {"id": "b", "content": "task B", "status": "in_progress"},
        ])
        assert len(mgr.items) == 2
        assert mgr.items[0].id == "a"
        assert mgr.items[1].status == "in_progress"

    def test_write_upserts_existing(self):
        mgr = TodoManager()
        mgr.write([{"id": "a", "content": "task A", "status": "pending"}])
        mgr.write([{"id": "a", "content": "task A updated", "status": "completed"}])
        assert len(mgr.items) == 1
        assert mgr.items[0].content == "task A updated"
        assert mgr.items[0].status == "completed"

    def test_write_preserves_order_of_existing_and_new(self):
        mgr = TodoManager()
        mgr.write([
            {"id": "a", "content": "A", "status": "pending"},
            {"id": "b", "content": "B", "status": "pending"},
        ])
        mgr.write([
            {"id": "b", "content": "B+", "status": "completed"},
            {"id": "c", "content": "C", "status": "pending"},
        ])
        ids = [item.id for item in mgr.items]
        assert ids == ["a", "b", "c"]


class TestTodoManagerValidation:
    def test_rejects_over_max_items(self):
        mgr = TodoManager()
        todos = [{"id": str(i), "content": f"task {i}", "status": "pending"} for i in range(21)]
        with pytest.raises(ValueError, match="Max 20 todos"):
            mgr.write(todos)

    def test_rejects_multiple_in_progress(self):
        mgr = TodoManager()
        with pytest.raises(ValueError, match="Only one task can be in_progress"):
            mgr.write([
                {"id": "a", "content": "A", "status": "in_progress"},
                {"id": "b", "content": "B", "status": "in_progress"},
            ])

    def test_rejects_empty_content(self):
        mgr = TodoManager()
        with pytest.raises(ValueError, match="content required"):
            mgr.write([{"id": "a", "content": "", "status": "pending"}])

    def test_rejects_invalid_status(self):
        mgr = TodoManager()
        with pytest.raises(ValueError, match="invalid status"):
            mgr.write([{"id": "a", "content": "A", "status": "unknown"}])

    def test_accepts_exactly_max_items(self):
        mgr = TodoManager()
        todos = [{"id": str(i), "content": f"task {i}", "status": "pending"} for i in range(20)]
        mgr.write(todos)
        assert len(mgr.items) == 20

    def test_accepts_single_in_progress(self):
        mgr = TodoManager()
        mgr.write([
            {"id": "a", "content": "A", "status": "in_progress"},
            {"id": "b", "content": "B", "status": "pending"},
        ])
        assert mgr.items[0].status == "in_progress"


class TestTodoManagerRender:
    def test_render_empty(self):
        mgr = TodoManager()
        assert mgr.render() == "(no todos)"

    def test_render_shows_icons(self):
        mgr = TodoManager()
        mgr.write([
            {"id": "a", "content": "task A", "status": "pending"},
            {"id": "b", "content": "task B", "status": "in_progress"},
            {"id": "c", "content": "task C", "status": "completed"},
        ])
        text = mgr.render()
        assert "[ ] task A" in text
        assert "[>] task B" in text
        assert "[x] task C" in text

    def test_render_shows_completion_count(self):
        mgr = TodoManager()
        mgr.write([
            {"id": "a", "content": "A", "status": "completed"},
            {"id": "b", "content": "B", "status": "pending"},
            {"id": "c", "content": "C", "status": "completed"},
        ])
        assert "(2/3 completed)" in mgr.render()
