"""Spoken editing commands and the glossary. Run: .venv/bin/python -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.backends import apply_voice_commands as vc, clean_text  # noqa: E402

CLEANUP = {"kind": "glossary", "rules": "glossary/en.json"}  # what the app uses by default


class FiresOn(unittest.TestCase):
    def test_scratch_that_drops_previous_sentence(self):
        self.assertEqual(vc("Write a script to list pods. Scratch that. Write a script to list nodes."),
                         "Write a script to list nodes.")

    def test_keeps_earlier_sentences(self):
        self.assertEqual(vc("Explain DNS. Add an example. Scratch that. Add a diagram."),
                         "Explain DNS. Add a diagram.")

    def test_delete_everything_and_start_from_here(self):
        self.assertEqual(vc("Write a script to list pods. Delete everything and start from here. Explain DNS."),
                         "Explain DNS.")

    def test_start_over(self):
        self.assertEqual(vc("Explain DNS. Start over. Explain TLS."), "Explain TLS.")

    def test_command_first_in_text(self):
        self.assertEqual(vc("Start over, explain TLS."), "Explain TLS.")

    def test_comma_separated_scratch_that(self):
        self.assertEqual(vc("Create a bucket in us-east-1, scratch that, in eu-west-1."), "In eu-west-1.")

    def test_command_at_end_clears_everything_it_cancels(self):
        self.assertEqual(vc("Explain DNS. Scratch that."), "")

    def test_two_commands(self):
        self.assertEqual(vc("A one. B two. Scratch that. C three. Scratch that. D four."), "A one. D four.")

    def test_leading_retraction_words_are_part_of_the_command(self):
        # a real dictation: "No, never mind." must not be taken as the sentence to delete
        self.assertEqual(vc('This is a test. I want you to write 1, 2, 3. No, never mind. Scratch that. '
                            'Don\'t write it. Write "hello world".'),
                         'This is a test. Don\'t write it. Write "hello world".')

    def test_case_insensitive(self):
        self.assertEqual(vc("Explain DNS. SCRATCH THAT. Explain TLS."), "Explain TLS.")


class EndToEnd(unittest.TestCase):
    """Through the same function the app calls: commands, then glossary, then capital letter."""

    def test_user_scenario(self):
        out = clean_text(CLEANUP, "Write a script to delete the pods. Delete everything and start from here. "
                                  "cube control get pods in the kube deck namespace.")
        self.assertEqual(out, "kubectl get pods in the Kube Deck namespace.")

    def test_scratch_then_capitalised(self):
        self.assertEqual(clean_text(CLEANUP, "Explain DNS. Scratch that, explain TLS."), "Explain TLS.")

    def test_tool_name_not_capitalised_after_a_cut(self):
        self.assertEqual(clean_text(CLEANUP, "Explain DNS. Start over. Cube control get nodes."), "kubectl get nodes.")

    def test_dictation_starting_with_a_tool_name_is_not_capitalised(self):
        self.assertEqual(clean_text(CLEANUP, "cube control get pods in the staging namespace."),
                         "kubectl get pods in the staging namespace.")

    def test_commands_can_be_switched_off(self):
        text = "Explain DNS. Scratch that. Explain TLS."
        self.assertEqual(clean_text({**CLEANUP, "voice_commands": False}, text), text)


class LeavesAlone(unittest.TestCase):
    """If one of these changed, the feature would be eating real dictation."""

    def test_command_words_used_as_content(self):
        for text in ["I think we should delete that file.",
                     "Please delete that.",   # "delete that." inside a sentence, not its own clause
                     "Can you scratch that itch for me?",
                     "Delete everything in the namespace.",
                     "Should I start over with a new cluster?",
                     "We forget that sometimes.",
                     "Clear everything older than a day from the cache."]:
            self.assertEqual(vc(text), text, text)

    def test_retraction_words_alone_do_nothing(self):
        for text in ["No, we need that.", "Wait for the pod to start.", "Never mind the logs. Check the events."]:
            self.assertEqual(vc(text), text, text)

    def test_plain_text_untouched(self):
        self.assertEqual(vc("Explain the difference between a Pod and a Deployment."),
                         "Explain the difference between a Pod and a Deployment.")


if __name__ == "__main__":
    unittest.main()
