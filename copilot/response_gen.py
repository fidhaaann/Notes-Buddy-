"""
copilot/response_gen.py
Natural response generation for the copilot.

Wraps backend results with conversational framing, adds predicted
next-action suggestions, and handles off-topic gracefully.

Keeps responses concise, student-focused, and professional.
"""

from __future__ import annotations

from typing import Optional


def with_suggestions(base_response: str, suggestions: list[str]) -> str:
    """Append predicted action suggestions to a response.
    
    Only appends if there are suggestions and they're meaningful.
    """
    if not suggestions:
        return base_response

    lines = [base_response, "", "─" * 34, ""]
    lines.append("💡 Suggested Actions:")
    for suggestion in suggestions[:4]:
        lines.append(f"  • {suggestion}")
    return "\n".join(lines)


def off_topic_response(topic: str, redirect_query: str = "") -> str:
    """Generate a gentle redirect for off-topic questions."""
    lines = [
        "I'm primarily a Google Drive assistant 📁",
        "",
    ]
    if redirect_query:
        lines.append(f"Would you like me to search your Drive for \"{redirect_query}\"?")
    else:
        lines.extend([
            "I can help you with:",
            "  • Finding and organizing files",
            "  • Searching notes by topic",
            "  • Uploading and downloading",
            "  • Managing your Drive folders",
            "",
            "What would you like to do?",
        ])
    return "\n".join(lines)


def slot_prompt(prompt: str) -> str:
    """Format a slot-filling clarification prompt."""
    return f"🤔 {prompt}"


def copilot_error(reason: str) -> str:
    """Generate an honest error response."""
    return (
        f"I couldn't complete that request.\n"
        f"\n"
        f"  {reason}\n"
        f"\n"
        f"Try rephrasing, or say \"help\" to see what I can do."
    )


def no_results_honest(query: str) -> str:
    """Honest response when no matching files are found."""
    return (
        f"I couldn't find any files matching \"{query}\".\n"
        f"\n"
        f"  This could mean:\n"
        f"  • The file doesn't exist in your Drive\n"
        f"  • It may be in a folder I haven't indexed yet\n"
        f"\n"
        f"  Try:\n"
        f"  • Browse your folders manually\n"
        f"  • Say \"index this folder\" to build the search index\n"
        f"  • Use different keywords"
    )


def feature_not_available(feature: str) -> str:
    """Honest response for unsupported features."""
    return (
        f"That feature is not available yet.\n"
        f"\n"
        f"  Requested: {feature}\n"
        f"\n"
        f"  Let me know if there's something else I can help with."
    )


def action_suggestions_for_intent(intent: str, context: str = "") -> list[str]:
    """Generate predicted next-action suggestions based on completed intent."""
    _SUGGESTIONS: dict[str, list[str]] = {
        "search": [
            "Download a file from the results",
            "Show file details",
            "Open the containing folder",
            "Search for something else",
        ],
        "browse": [
            "Open a folder",
            "Download a file",
            "Search for notes",
            "Create a new folder",
        ],
        "download": [
            "Download another file",
            "Show recent files",
            "Go back to the folder",
            "Search for more files",
        ],
        "upload": [
            "Browse your files",
            "Upload another file",
            "Open the folder",
            "Show recent uploads",
        ],
        "open_folder": [
            "Browse files inside",
            "Search within this folder",
            "Upload a file here",
            "Go back",
        ],
        "mkdir": [
            "Open the new folder",
            "Upload files to it",
            "Browse current folder",
            "Create another folder",
        ],
        "delete": [
            "Browse the folder",
            "Show recent files",
            "Search for files",
        ],
        "rename": [
            "Show file details",
            "Download the file",
            "Browse the folder",
        ],
        "favorites": [
            "Download a favorite file",
            "Show file details",
            "Remove from favorites",
        ],
        "recent": [
            "Download a recent file",
            "Show file details",
            "Search for notes",
        ],
    }
    return _SUGGESTIONS.get(intent, [])
