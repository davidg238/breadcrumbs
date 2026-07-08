import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import session_recorder  # noqa: E402

classify = session_recorder.classify_user_entry


def entry(content, is_meta=False):
    """Build a minimal role=user transcript entry."""
    e = {"type": "user", "message": {"role": "user", "content": content}}
    if is_meta:
        e["isMeta"] = True
    return e


class ClassifyUserEntryTests(unittest.TestCase):
    # --- genuinely typed prompts stay "user" ---

    def test_plain_string_prompt_is_user(self):
        c = "resume, invoke writing-plans for the first-step plan"
        self.assertEqual(classify(entry(c), tool_result=None, content_text=c), "user")

    def test_list_of_text_prompt_is_user(self):
        # Typed prompt delivered as a content list of text blocks (no tool_result,
        # not meta, no injection tag) must still be attributed to the user.
        blocks = [{"type": "text", "text": "why is virtually nothing I typed shown?"}]
        ct = "why is virtually nothing I typed shown?"
        self.assertEqual(classify(entry(blocks), tool_result=None, content_text=ct), "user")

    def test_prompt_with_appended_system_reminder_is_user(self):
        # User words come first; a trailing reminder must not demote the message.
        ct = "run to end\n<system-reminder>background context</system-reminder>"
        self.assertEqual(classify(entry(ct), tool_result=None, content_text=ct), "user")

    def test_image_only_prompt_is_user(self):
        self.assertEqual(classify(entry([]), tool_result=None, content_text=None), "user")

    # --- injected string content is NOT the user ---

    def test_task_notification_is_injection(self):
        ct = "<task-notification> <task-id>a9f</task-id> ... </task-notification>"
        self.assertEqual(
            classify(entry(ct), tool_result=None, content_text=ct), "system_injection")

    def test_slash_command_expansion_is_injection(self):
        ct = "<command-name>/model</command-name>"
        self.assertEqual(
            classify(entry(ct), tool_result=None, content_text=ct), "system_injection")

    def test_local_command_stdout_is_injection(self):
        ct = "<local-command-stdout>Set model to Opus</local-command-stdout>"
        self.assertEqual(
            classify(entry(ct), tool_result=None, content_text=ct), "system_injection")

    def test_meta_skill_load_is_injection(self):
        blocks = [{"type": "text", "text": "Base directory for this skill: /home/..."}]
        ct = "Base directory for this skill: /home/..."
        self.assertEqual(
            classify(entry(blocks, is_meta=True), tool_result=None, content_text=ct),
            "system_injection")

    # --- tool results ---

    def test_tool_result_is_tool_result(self):
        self.assertEqual(
            classify(entry([]), tool_result="output", content_text="output"),
            "tool_result")

    # --- a user genuinely typing a leading angle bracket that is NOT a known tag ---

    def test_unknown_tag_prompt_is_user(self):
        ct = "<div> how do I render this in the viewer?"
        self.assertEqual(classify(entry(ct), tool_result=None, content_text=ct), "user")


if __name__ == "__main__":
    unittest.main()
