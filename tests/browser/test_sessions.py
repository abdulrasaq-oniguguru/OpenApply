from pathlib import Path

from openapply.browser.sessions import BrowserSessionStatus, BrowserSessionStore


def test_session_metadata_never_stores_url_query_or_credentials(tmp_path: Path) -> None:
    store = BrowserSessionStore(
        profile_dir=tmp_path / "profile",
        metadata_path=tmp_path / "session.json",
    )

    store.mark_ready("https://jobs.example.com/login?token=secret-value")

    saved = store.metadata_path.read_text(encoding="utf-8")
    assert store.status().ready
    assert "https://jobs.example.com/" in saved
    assert "secret-value" not in saved
    assert "token" not in saved


def test_opening_session_with_dead_process_is_not_ready(tmp_path: Path) -> None:
    store = BrowserSessionStore(
        profile_dir=tmp_path / "profile",
        metadata_path=tmp_path / "session.json",
    )
    store.write(BrowserSessionStatus(state="opening", pid=999_999_999))

    status = store.status()

    assert status.state == "error"
    assert not status.ready


def test_forget_removes_only_the_dedicated_profile(tmp_path: Path) -> None:
    store = BrowserSessionStore(
        profile_dir=tmp_path / "browser-profile",
        metadata_path=tmp_path / "browser-session.json",
    )
    store.profile_dir.mkdir()
    (store.profile_dir / "Cookies").write_text("private", encoding="utf-8")
    unrelated = tmp_path / "keep.txt"
    unrelated.write_text("keep", encoding="utf-8")
    store.mark_ready("https://jobs.example.com/login")

    store.clear()

    assert not store.profile_dir.exists()
    assert not store.metadata_path.exists()
    assert unrelated.read_text(encoding="utf-8") == "keep"
