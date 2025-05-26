# pylint: disable=missing-module-docstring
import functools
import json
import logging
import math
import os
import re
import time
from functools import cached_property
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple, Type, TypeVar

# https://github.com/prius/python-leetcode
import leetcode.api.default_api  # type: ignore
import leetcode.api_client  # type: ignore
import leetcode.auth  # type: ignore
import leetcode.configuration  # type: ignore
import leetcode.models.graphql_query  # type: ignore
import leetcode.models.graphql_query_get_question_detail_variables  # type: ignore
import leetcode.models.graphql_query_problemset_question_list_variables  # type: ignore
import leetcode.models.graphql_query_problemset_question_list_variables_filter_input  # type: ignore
import leetcode.models.graphql_question_detail  # type: ignore
import urllib3  # type: ignore
from PIL import Image, ImageOps
from selenium.webdriver.common.by import By
from tqdm import tqdm  # type: ignore

CACHE_DIR = "cache"

FAVORITES_SLUG = os.getenv("FAVORITES_SLUG", "facebook-all")
ADD_TO_LIST = bool(int(os.getenv("ADD_TO_LIST", "0") or 0))
SAVED_LIST_FAVORITES_SLUG = os.getenv("SAVED_FAVORITES_SLUG", "nlsp6tm6")


def _get_leetcode_api_client() -> leetcode.api.default_api.DefaultApi:
    """
    Leetcode API instance constructor.

    This is a singleton, because we don't need to create a separate client
    each time
    """

    configuration = leetcode.configuration.Configuration()
    session_id = os.environ["LEETCODE_SESSION"]
    csrf_token = os.environ["csrftoken"]

    configuration.api_key["x-csrftoken"] = csrf_token
    configuration.api_key["csrftoken"] = csrf_token
    configuration.api_key["LEETCODE_SESSION"] = session_id
    configuration.api_key["Referer"] = "https://leetcode.com"
    configuration.debug = False
    api_instance = leetcode.api.default_api.DefaultApi(
        leetcode.api_client.ApiClient(configuration)
    )

    return api_instance


_T = TypeVar("_T")


class _RetryDecorator:
    _times: int
    _exceptions: Tuple[Type[Exception]]
    _delay: float

    def __init__(
        self, times: int, exceptions: Tuple[Type[Exception]], delay: float
    ) -> None:
        self._times = times
        self._exceptions = exceptions
        self._delay = delay

    def __call__(self, func: Callable[..., _T]) -> Callable[..., _T]:
        times: int = self._times
        exceptions: Tuple[Type[Exception]] = self._exceptions
        delay: float = self._delay

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> _T:
            for attempt in range(times - 1):
                try:
                    return func(*args, **kwargs)
                except exceptions:
                    logging.exception(
                        "Exception occured, try %s/%s", attempt + 1, times
                    )
                    time.sleep(delay)

            logging.error("Last try")
            return func(*args, **kwargs)

        return wrapper


import mistune

from selenium import webdriver


def retry(
    times: int, exceptions: Tuple[Type[Exception]], delay: float
) -> _RetryDecorator:
    """
    Retry Decorator
    Retries the wrapped function/method `times` times if the exceptions listed
    in `exceptions` are thrown
    """

    return _RetryDecorator(times, exceptions, delay)


class LeetcodeData:
    """
    Retrieves and caches the data for problems, acquired from the leetcode API.

    This data can be later accessed using provided methods with corresponding
    names.
    """

    def __init__(
        self, start: int, stop: int, page_size: int = 1000, list_id: str = ""
    ) -> None:
        """
        Initialize leetcode API and disk cache for API responses
        """
        if start < 0:
            raise ValueError(f"Start must be non-negative: {start}")

        if stop < 0:
            raise ValueError(f"Stop must be non-negative: {start}")

        if page_size < 0:
            raise ValueError(f"Page size must be greater than 0: {page_size}")

        if start > stop:
            raise ValueError(f"Start (){start}) must be not greater than stop ({stop})")

        self._start = start
        self._stop = stop
        self._page_size = page_size
        self._list_id = list_id

    @cached_property
    def _api_instance(self) -> leetcode.api.default_api.DefaultApi:
        return _get_leetcode_api_client()

    @cached_property
    def _cache(
        self,
    ) -> Dict[str, leetcode.models.graphql_question_detail.GraphqlQuestionDetail]:
        """
        Cached method to return dict (problem_slug -> question details)
        """
        problems = self._get_problems_data()
        return {problem.title_slug: problem for problem in problems}

    def get_favorite_questions_list(
        self, api_instance, favorites_slug, skip=0, limit=250
    ):
        graphql_request = {
            "query": """
        query favoriteQuestionList($favoriteSlug: String!, $filter: FavoriteQuestionFilterInput, $filtersV2: QuestionFilterInput, $searchKeyword: String, $sortBy: QuestionSortByInput, $limit: Int, $skip: Int, $version: String = "v2") {
          favoriteQuestionList(
            favoriteSlug: $favoriteSlug
            filter: $filter
            filtersV2: $filtersV2
            searchKeyword: $searchKeyword
            sortBy: $sortBy
            limit: $limit
            skip: $skip
            version: $version
          ) {
            questions {
              difficulty
              id
              paidOnly
              questionFrontendId
              status
              title
              titleSlug
              translatedTitle
              isInMyFavorites
              frequency
              acRate
              topicTags {
                name
                nameTranslated
                slug
              }
            }
            totalLength
            hasMore
          }
        }
            """,
            "variables": {
                "skip": skip,
                "limit": limit,
                "favoriteSlug": favorites_slug,
                "filtersV2": {
                    "filterCombineType": "ALL",
                    "statusFilter": {"questionStatuses": [], "operator": "IS"},
                    "difficultyFilter": {
                        "difficulties": ["MEDIUM", "HARD"],
                        "operator": "IS",
                    },
                    "languageFilter": {"languageSlugs": [], "operator": "IS"},
                    "topicFilter": {"topicSlugs": [], "operator": "IS"},
                    "acceptanceFilter": {},
                    "frequencyFilter": {},
                    "lastSubmittedFilter": {},
                    "publishedFilter": {},
                    "companyFilter": {"companySlugs": [], "operator": "IS"},
                    "positionFilter": {"positionSlugs": [], "operator": "IS"},
                    "premiumFilter": {"premiumStatus": [], "operator": "IS"},
                },
                "searchKeyword": "",
                "sortBy": {"sortField": "FREQUENCY", "sortOrder": "DESCENDING"},
            },
            "operationName": "favoriteQuestionList",
        }

        time.sleep(2)  # Leetcode has a rate limiter
        data = api_instance.graphql_post(
            body=graphql_request, _preload_content=False
        ).data
        data = json.loads(data.decode())["data"]["favoriteQuestionList"]
        questions = {}
        for q in data["questions"]:
            questions[q["titleSlug"]] = q
        data["questions"] = questions
        return data

    def add_to_question_list(
        self, favorite_questions_list_data, api_instance, saved_list_favorites_slug
    ):
        question_slugs = list(favorite_questions_list_data["questions"].keys())
        add_question_to_list = {
            "query": """
            mutation batchAddQuestionsToFavorite($favoriteSlug: String!, $questionSlugs: [String]!) {
              batchAddQuestionsToFavorite(
                favoriteSlug: $favoriteSlug
                questionSlugs: $questionSlugs
              ) {
                ok
                error
              }
            }
            """,
            "variables": {
                "favoriteSlug": saved_list_favorites_slug,
                "questionSlugs": question_slugs,
            },
            "operationName": "batchAddQuestionsToFavorite",
        }
        api_instance.graphql_post(body=add_question_to_list)

    @retry(times=3, exceptions=(urllib3.exceptions.ProtocolError,), delay=5)
    def _get_problems_count(self) -> int:
        api_instance = self._api_instance
        self.favorite_questions_list_data = self.get_favorite_questions_list(
            api_instance, FAVORITES_SLUG
        )
        if ADD_TO_LIST:
            self.add_to_question_list(
                self.favorite_questions_list_data,
                api_instance,
                SAVED_LIST_FAVORITES_SLUG,
            )
        return self.favorite_questions_list_data["totalLength"] or 0

    @retry(times=3, exceptions=(urllib3.exceptions.ProtocolError,), delay=5)
    def _get_problems_data_page(
        self, offset: int, page_size: int, page: int
    ) -> List[leetcode.models.graphql_question_detail.GraphqlQuestionDetail]:
        api_instance = self._api_instance
        graphql_request = leetcode.models.graphql_query.GraphqlQuery(
            query="""
            query problemsetQuestionList($categorySlug: String, $limit: Int, $skip: Int, $filters: QuestionListFilterInput) {
              problemsetQuestionList: questionList(
                categorySlug: $categorySlug
                limit: $limit
                skip: $skip
                filters: $filters
              ) {
                questions: data {
                    questionFrontendId
                    title
                    titleSlug
                    categoryTitle
                    freqBar
                    content
                    isPaidOnly
                    difficulty
                    likes
                    dislikes
                    topicTags {
                      name
                      slug
                    }
                    stats
                    hints
                }
              }
            }
            """,
            variables=leetcode.models.graphql_query_problemset_question_list_variables.GraphqlQueryProblemsetQuestionListVariables(
                category_slug="",
                limit=page_size,
                skip=offset + page * page_size,
                filters=leetcode.models.graphql_query_problemset_question_list_variables_filter_input.GraphqlQueryProblemsetQuestionListVariablesFilterInput(
                    list_id=SAVED_LIST_FAVORITES_SLUG
                ),
            ),
            operation_name="problemsetQuestionList",
        )

        time.sleep(2)  # Leetcode has a rate limiter
        data = api_instance.graphql_post(
            body=graphql_request
        ).data.problemset_question_list.questions

        for d in data:
            d.frequency = self.favorite_questions_list_data["questions"][d.title_slug][
                "frequency"
            ]

        return data

    def _get_problems_data(
        self,
    ) -> List[leetcode.models.graphql_question_detail.GraphqlQuestionDetail]:
        problem_count = self._get_problems_count()

        if self._start > problem_count:
            raise ValueError(
                "Start ({self._start}) is greater than problems count ({problem_count})"
            )

        start = self._start
        stop = min(self._stop, problem_count)

        page_size = min(self._page_size, stop - start + 1)

        problems: List[
            leetcode.models.graphql_question_detail.GraphqlQuestionDetail
        ] = []

        logging.info("Fetching %s problems %s per page", stop - start + 1, page_size)

        for page in tqdm(
            range(math.ceil((stop - start + 1) / page_size)),
            unit="problem",
            unit_scale=page_size,
        ):
            data = self._get_problems_data_page(start, page_size, page)
            problems.extend(data)

        return problems

    async def all_problems_handles(self) -> List[str]:
        """
        Get all problem handles known.

        Example: ["two-sum", "three-sum"]
        """
        return list(self._cache.keys())

    def _get_problem_data(
        self, problem_slug: str
    ) -> leetcode.models.graphql_question_detail.GraphqlQuestionDetail:
        """
        TODO: Legacy method. Needed in the old architecture. Can be replaced
        with direct cache calls later.
        """
        cache = self._cache
        if problem_slug in cache:
            return cache[problem_slug]

        raise ValueError(f"Problem {problem_slug} is not in cache")

    async def _get_description(self, problem_slug: str) -> str:
        """
        Problem description
        """
        data = self._get_problem_data(problem_slug)
        return data.content or "No content"

    async def _stats(self, problem_slug: str) -> Dict[str, str]:
        """
        Various stats about problem. Such as number of accepted solutions, etc.
        """
        data = self._get_problem_data(problem_slug)
        return json.loads(data.stats)

    async def submissions_total(self, problem_slug: str) -> int:
        """
        Total number of submissions of the problem
        """
        return int((await self._stats(problem_slug))["totalSubmissionRaw"])

    async def submissions_accepted(self, problem_slug: str) -> int:
        """
        Number of accepted submissions of the problem
        """
        return int((await self._stats(problem_slug))["totalAcceptedRaw"])

    async def description(self, problem_slug: str) -> str:
        """
        Problem description
        """
        return await self._get_description(problem_slug)

    async def difficulty(self, problem_slug: str) -> str:
        """
        Problem difficulty. Returns colored HTML version, so it can be used
        directly in Anki
        """
        data = self._get_problem_data(problem_slug)
        diff = data.difficulty

        if diff in {"Easy", "EASY"}:
            return "<font color='green'>Easy</font>"

        if diff in {"Medium", "MEDIUM"}:
            return "<font color='orange'>Medium</font>"

        if diff in {"Hard", "HARD"}:
            return "<font color='red'>Hard</font>"

        raise ValueError(f"Incorrect difficulty: {diff}")

    async def paid(self, problem_slug: str) -> str:
        """
        Problem's "available for paid subsribers" status
        """
        data = self._get_problem_data(problem_slug)
        return data.is_paid_only

    async def problem_id(self, problem_slug: str) -> str:
        """
        Numerical id of the problem
        """
        data = self._get_problem_data(problem_slug)
        return data.question_frontend_id

    async def likes(self, problem_slug: str) -> int:
        """
        Number of likes for the problem
        """
        data = self._get_problem_data(problem_slug)
        likes = data.likes

        if not isinstance(likes, int):
            raise ValueError(f"Likes should be int: {likes}")

        return likes

    async def dislikes(self, problem_slug: str) -> int:
        """
        Number of dislikes for the problem
        """
        data = self._get_problem_data(problem_slug)
        dislikes = data.dislikes

        if not isinstance(dislikes, int):
            raise ValueError(f"Dislikes should be int: {dislikes}")

        return dislikes

    async def tags(self, problem_slug: str) -> List[str]:
        """
        List of the tags for this problem (string slugs)
        """
        data = self._get_problem_data(problem_slug)
        tags = list(map(lambda x: x.slug, data.topic_tags))
        tags.append(f"difficulty-{data.difficulty.lower()}-tag")
        return tags

    async def freq_bar(self, problem_slug: str) -> float:
        """
        Returns percentage for frequency bar
        """
        data = self._get_problem_data(problem_slug)
        return data.freq_bar or 0

    async def title(self, problem_slug: str) -> float:
        """
        Returns problem title
        """
        data = self._get_problem_data(problem_slug)
        return data.title

    async def frequency(self, problem_slug: str) -> float:
        """
        Returns problem title
        """
        data = self._get_problem_data(problem_slug)
        return str(int(data.frequency))

    async def category(self, problem_slug: str) -> float:
        """
        Returns problem category title
        """
        data = self._get_problem_data(problem_slug)
        return data.category_title

    async def hints(self, problem_slug: str) -> float:
        """
        Returns problem category title
        """
        data = self._get_problem_data(problem_slug)
        return data.hints

    browser = webdriver.Chrome()

    async def solution(self, problem_slug: str) -> str:
        query = """
        query ugcArticleOfficialSolutionArticle($questionSlug: String!) {
          ugcArticleOfficialSolutionArticle(questionSlug: $questionSlug) {
            content
          }
        }
        """

        graphql_request = {
            "query": query,
            "variables": {"questionSlug": problem_slug},
            "operationName": "ugcArticleOfficialSolutionArticle",
        }
        api_instance = self._api_instance
        data = api_instance.graphql_post(body=graphql_request, _preload_content=False)
        if data:
            try:
                data = data.json()
                rawmd = data["data"]["ugcArticleOfficialSolutionArticle"]["content"]
                rawmd = re.sub(
                    "<lcvideo>.*</lcvideo>", "", rawmd, flags=re.MULTILINE | re.DOTALL
                )
                rawmd = re.sub("!?!.*!?!", "", rawmd)
                rawmd = rawmd.replace("## Video Solution\n", "")
                rawmd = rawmd.replace(
                    "../Figures",
                    f"https://leetcode.com/problems/{problem_slug}/Figures",
                )
                iframes = list(
                    re.finditer(
                        r'<iframe src="https://leetcode.com/playground/(.*?)" .* height="(\d+)" name="(\w+)"></iframe>',
                        rawmd,
                    )
                )
                iframe_images = []
                if iframes:
                    for iframe in iframes[::-1]:
                        pth = Path(__file__).parent.parent / problem_slug
                        pth.mkdir(exist_ok=True)
                        pth /= f"{iframe.group(3)}.png"
                        if not pth.exists():
                            self.browser.get(
                                f"https://leetcode.com/playground/{iframe.group(1)}"
                            )
                            time.sleep(2)
                            try:
                                python_button = self.browser.find_element(
                                    by=By.XPATH,
                                    value="""//*[@id="app"]/div/div[1]/div[1]/div/button[text()='Python3']""",
                                )
                                python_button.click()
                            except:
                                pass
                            code = self.browser.find_element(
                                by=By.CLASS_NAME, value="CodeMirror-code"
                            )
                            lines = self.browser.find_elements(
                                by=By.XPATH, value="//span[@role='presentation']"
                            )
                            rect = code.rect
                            rect["width"] = max([l.size["width"] for l in lines])
                            self.browser.save_screenshot(str(pth))
                            im = Image.open(pth)
                            im = im.crop(
                                (
                                    rect["x"],
                                    rect["y"],
                                    rect["width"] + rect["x"],
                                    rect["height"] + rect["y"],
                                )
                            )
                            img_with_border = ImageOps.expand(im, border=1)
                            img_with_border.save(pth, "PNG")

                        iframe_images.append(pth)
                        old = rawmd[iframe.span()[0] : iframe.span()[1]]
                        rawmd = rawmd.replace(old, f'<img src="{iframe.group(3)}.png">')
                html = mistune.html(rawmd)
                html = re.sub(r"\$\$(.*?)\$\$", r"\\(\1\\)", html)
                html = re.sub(r"\$(.*?)\$", r"\\(\1\\)", html)
                return html, iframe_images
            except Exception as e:
                print(problem_slug, e)
                return None
        else:
            return None
