#!/usr/bin/env python3
"""
This script generates an Anki deck with all the leetcode problems currently
known.
"""

import argparse
import asyncio
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Coroutine, List

# https://github.com/kerrickstaley/genanki
import genanki  # type: ignore
from tqdm import tqdm  # type: ignore

import leetcode_anki.helpers.leetcode

LEETCODE_ANKI_MODEL_ID = 4567610856
LEETCODE_ANKI_DECK_ID = 8589798175
OUTPUT_FILE = "leetcode.apkg"
ALLOWED_EXTENSIONS = {".py", ".go"}


logging.getLogger().setLevel(logging.INFO)


def parse_args() -> argparse.Namespace:
    """
    Parse command line arguments for the script
    """
    parser = argparse.ArgumentParser(description="Generate Anki cards for leetcode")
    parser.add_argument(
        "--start", type=int, help="Start generation from this problem", default=0
    )
    parser.add_argument(
        "--stop", type=int, help="Stop generation on this problem", default=2**64
    )
    parser.add_argument(
        "--page-size",
        type=int,
        help="Get at most this many problems (decrease if leetcode API times out)",
        default=500,
    )
    parser.add_argument(
        "--list-id",
        type=str,
        help="Get all questions from a specific list id (https://leetcode.com/list?selectedList=<list_id>",
        default="",
    )
    parser.add_argument(
        "--output-file", type=str, help="Output filename", default=OUTPUT_FILE
    )

    args = parser.parse_args()

    return args


class LeetcodeNote(genanki.Note):
    """
    Extended base class for the Anki note, that correctly sets the unique
    identifier of the note.
    """

    @property
    def guid(self) -> str:
        # Hash by leetcode task handle
        return genanki.guid_for(self.fields[0])


async def generate_anki_note(
    leetcode_data: leetcode_anki.helpers.leetcode.LeetcodeData,
    leetcode_model: genanki.Model,
    leetcode_task_handle: str,
) -> LeetcodeNote:
    """
    Generate a single Anki flashcard
    """

    fields = [
                 leetcode_task_handle,
                 str(await leetcode_data.problem_id(leetcode_task_handle)),
                 str(await leetcode_data.title(leetcode_task_handle)),
                 # str(await leetcode_data.category(leetcode_task_handle)),
                 await leetcode_data.description(leetcode_task_handle),
                 await leetcode_data.difficulty(leetcode_task_handle),
                 # "yes" if await leetcode_data.paid(leetcode_task_handle) else "no",
                 # str(await leetcode_data.likes(leetcode_task_handle)),
                 # str(await leetcode_data.dislikes(leetcode_task_handle)),
                 # str(await leetcode_data.submissions_total(leetcode_task_handle)),
                 # str(await leetcode_data.submissions_accepted(leetcode_task_handle)),
                 # str(
                 #     int(
                 #         await leetcode_data.submissions_accepted(leetcode_task_handle)
                 #         / await leetcode_data.submissions_total(leetcode_task_handle)
                 #         * 100
                 #     )
                 # ),
                 # str(await leetcode_data.freq_bar(leetcode_task_handle)),
             ]

    hints = [str(hint) for hint in await leetcode_data.hints(leetcode_task_handle)]
    if len(hints) > 5:
        raise Exception("not enough hints")
    for i in range(5):
        if i < len(hints):
            fields.append(hints[i])
        else:
            fields.append("")

    solution = await leetcode_data.solution(leetcode_task_handle)
    iframes = []
    if solution is not None:
        html, iframes = solution
        fields.append(html)
    else:
        fields.append("")

    return LeetcodeNote(
        model=leetcode_model,
        fields=fields,
        tags=await leetcode_data.tags(leetcode_task_handle),
        # FIXME: sort field doesn't work doesn't work
        sort_field=str(await leetcode_data.freq_bar(leetcode_task_handle)).zfill(3),
    ), iframes


async def generate(
    start: int, stop: int, page_size: int, list_id: str, output_file: str
) -> None:
    """
    Generate an Anki deck
    """
    leetcode_model = genanki.Model(
        LEETCODE_ANKI_MODEL_ID,
        "Leetcode model",
        fields=[
            {"name": "Slug"},
            {"name": "Id"},
            {"name": "Title"},
            # {"name": "Topic"},
            {"name": "Content"},
            {"name": "Difficulty"},
            # {"name": "Paid"},
            # {"name": "Likes"},
            # {"name": "Dislikes"},
            # {"name": "SubmissionsTotal"},
            # {"name": "SubmissionsAccepted"},
            # {"name": "SumissionAcceptRate"},
            # {"name": "Frequency"},
            # TODO: add hints
            {"name": "Hint1"},
            {"name": "Hint2"},
            {"name": "Hint3"},
            {"name": "Hint4"},
            {"name": "Hint5"},
            {"name": "Solution"},

        ],
        templates=[
            {
                "name": "Leetcode",
                "qfmt": """
                <h2>{{Id}}. {{Title}}</h2>
                <b>Difficulty:</b> {{Difficulty}}<br/>
                <b>URL:</b>
                <a href='https://leetcode.com/problems/{{Slug}}/'>
                    https://leetcode.com/problems/{{Slug}}/
                </a>
                <br/>
                <h3>Description</h3>
                {{Content}}
                </br>
                <h2>{{hint:Hint1}}</h2>
                </br>
                <h2>{{hint:Hint2}}</h2>
                </br>
                <h2>{{hint:Hint3}}</h2>
                </br>
                <h2>{{hint:Hint4}}</h2>
                </br>
                <h2>{{hint:Hint5}}</h2>
                </br>
                """,
                "afmt": """
                <hr id="answer">
                {{Solution}}
                """,
            }
        ],
    )
    leetcode_deck = genanki.Deck(LEETCODE_ANKI_DECK_ID, Path(output_file).stem)

    leetcode_data = leetcode_anki.helpers.leetcode.LeetcodeData(
        start, stop, page_size, list_id
    )

    note_generators: List[Awaitable[LeetcodeNote]] = []

    task_handles = await leetcode_data.all_problems_handles()

    logging.info("Generating flashcards")
    for leetcode_task_handle in task_handles:
        note_generators.append(
            generate_anki_note(leetcode_data, leetcode_model, leetcode_task_handle)
        )
    pack = genanki.Package(leetcode_deck)
    for leetcode_note in tqdm(note_generators, unit="flashcard"):
        leetcode_note, iframe_paths = await leetcode_note
        leetcode_deck.add_note(leetcode_note)
        pack.media_files += [str(f) for f in iframe_paths]

    pack.write_to_file(output_file)


async def main() -> None:
    """
    The main script logic
    """
    args = parse_args()

    start, stop, page_size, list_id, output_file = (
        args.start,
        args.stop,
        args.page_size,
        args.list_id,
        args.output_file,
    )
    await generate(start, stop, page_size, list_id, output_file)


if __name__ == "__main__":
    loop: asyncio.events.AbstractEventLoop = asyncio.get_event_loop()
    loop.run_until_complete(main())
