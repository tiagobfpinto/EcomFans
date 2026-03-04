import re

from youtube_transcript_api import YouTubeTranscriptApi


def extract_youtube_id(url):
    """Extract video ID from various YouTube URL formats."""
    patterns = [
        r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})",
        r"youtube\.com/shorts/([a-zA-Z0-9_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def fetch_transcript(youtube_id):
    """Fetch and join transcript segments into full text."""
    try:
        ytt_api = YouTubeTranscriptApi()
        transcript = ytt_api.fetch(youtube_id, languages=["en"])
        full_text = " ".join(segment.text for segment in transcript.snippets)
        return {"text": full_text, "success": True, "error": None}
    except Exception as e:
        return {"text": None, "success": False, "error": str(e)}
