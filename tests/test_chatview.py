"""The Ask kia chat page (kia.chatview). The script itself is syntax-checked in test_pages_js.py;
here we check the page keeps its safety invariants and shows chat actions when they're on.
"""

from kia import chatview


def test_the_page_never_uses_innerhtml():
    # Everything on the page goes in with textContent, because the reply, the tool names, and kia's
    # words for a request can all quote third-party playlist or guide text. Nothing is ever assigned
    # to innerHTML, which would be a way in for that text. (The word itself stays in a comment that
    # says as much, so we check for the assignment, not the mention.)
    assert "innerHTML =" not in chatview.SCRIPT


def test_the_read_only_intro_is_the_default():
    body = chatview.body()
    assert 'class="intro"' in body
    assert "can't change anything here" in body  # the plain read-only promise, before any server say-so


def test_actions_change_the_intro_and_show_approval_notes():
    script = chatview.SCRIPT
    # The page learns from the server whether actions are on and only then changes the intro.
    assert "d.actions" in script and "ACTIONS_INTRO" in script
    assert "refresh the guide" in script and "approve it on the dashboard" in script.lower()
    # An answer's requests are drawn as notes.
    assert "d.requests" in script and "requestNote" in script


def test_the_approval_link_uses_only_the_server_s_dashboard_url():
    # The only link the note makes points at approve_at, and only when it's a Dashboard https URL,
    # so a crafted request can't turn the note into a link somewhere else.
    script = chatview.SCRIPT
    assert 'link.href = at' in script
    assert 'at.indexOf("https://") === 0' in script
