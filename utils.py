from collections import defaultdict
import json
import math
import requests
from urlparse import parse_qs, urlsplit

import config
from models import Quiz


headers = {'Authorization': 'Bearer ' + config.API_KEY}
json_headers = {'Authorization': 'Bearer ' + config.API_KEY, 'Content-type': 'application/json'}


def extend_quiz(course_id, quiz, percent, user_id_list):
    """
    :param quiz: A quiz object from Canvas
    :type quiz: dict
    :param percent: The percent of original quiz time to be applied.
        e.g. 200 is double time, 100 is normal time, <100 is invalid.
    :type percent: int
    :param user_id_list: A list of Canvas user IDs to add time for.
    :type user_id_list: list
    :rtype: dict
    :returns: A dictionary with three parts:
        - success `bool` False if there was an error, True otherwise.
        - message `str` A long description of success or failure.
        - added_time `int` The amount of time added in minutes. Returns
        `None` if there was no time added.
    """
    quiz_id = quiz.get('id')
    time_limit = quiz.get('time_limit')

    if time_limit is None or time_limit < 1:
        msg = 'Quiz #{} has no time limit, so there is no time to add.'
        return {
            'success': True,
            'message': msg.format(quiz_id),
            'added_time': None
        }

    added_time = math.ceil(time_limit * ((float(percent)-100) / 100) if percent else 0)

    quiz_extensions = defaultdict(list)

    for user_id in user_id_list:
        user_extension = {
            'user_id': user_id,
            'extra_time': added_time
        }
        quiz_extensions['quiz_extensions'].append(user_extension)

    extensions_response = requests.post(
        "%scourses/%s/quizzes/%s/extensions" % (config.API_URL, course_id, quiz_id),
        data=json.dumps(quiz_extensions),
        headers=json_headers
    )

    if extensions_response.status_code == 200:
        msg = 'Successfully added {} minutes to quiz #{}'
        return {
            'success': True,
            'message': msg.format(added_time, quiz_id),
            'added_time': added_time
        }
    else:
        msg = 'Error creating extension for quiz #{}. Canvas status code: {}'
        return {
            'success': False,
            'message': msg.format(quiz_id, extensions_response.status_code),
            'added_time': None
        }


def get_quizzes(course_id, per_page=config.MAX_PER_PAGE):
    """
    Returns a list of all quizzes in the course.
    """
    quizzes = []
    quizzes_url = "%scourses/%s/quizzes?per_page=%d" % (config.API_URL, course_id, per_page)

    while True:
        quizzes_response = requests.get(quizzes_url, headers=headers)

        quizzes_list = quizzes_response.json()

        if 'errors' in quizzes_list:
            break

        if isinstance(quizzes_list, list):
            quizzes.extend(quizzes_list)
        else:
            quizzes = quizzes_list

        try:
            quizzes_url = quizzes_response.links['next']['url']
        except KeyError:
            break

    return quizzes


def search_users(course_id, per_page=config.DEFAULT_PER_PAGE, page=1, search_term=""):
    """
    Searches for students in the course.

    If no search term is provided, all users are returned.
    """
    users_url = "%s/courses/%s/search_users?per_page=%s&page=%s" % (
        config.API_URL,
        course_id,
        per_page,
        page
    )

    users_response = requests.get(
        users_url,
        data={
            'search_term': search_term,
            'enrollment_type': 'student'
        },
        headers=headers
    )
    user_list = users_response.json()

    if 'errors' in user_list:
        return [], 0

    num_pages = int(
        parse_qs(
            urlsplit(
                users_response.links['last']['url']
            ).query
        )['page'][0]
    )

    return user_list, num_pages


def get_user(user_id):
    """
    Get a user from canvas by id.

    :param user_id: ID of a Canvas user.
    :type user_id: int
    :rtype: dict
    :returns: A dictionary representation of a User in Canvas.
    """
    response = requests.get(config.API_URL + 'users/' + user_id, headers=headers)
    response.raise_for_status()

    return response.json()


def get_course(course_id):
    """
    Get a course from canvas by id.

    :param course_id: ID of a Canvas course.
    :type course_id: int
    :rtype: dict
    :returns: A dictionary representation of a Course in Canvas.
    """
    response = requests.get(config.API_URL + 'courses/' + course_id, headers=headers)
    response.raise_for_status()

    return response.json()


def get_or_create(session, model, **kwargs):
    """
    Simple version of Django's get_or_create for interacting with Models
    """
    instance = session.query(model).filter_by(**kwargs).first()
    if instance:
        return instance, False
    else:
        instance = model(**kwargs)
        session.add(instance)
        session.commit()
        return instance, True


def missing_quizzes(course_id, quickcheck=False):
    """
    Find all quizzes that are in Canvas but not in the database.

    :param course_id: The Canvas ID of the Course.
    :type course_id: int
    :param quickcheck: Setting this to `True` will return when the
        first missinq quiz is found.
    :type quickcheck: bool
    :rtype: list
    :returns: A list of dictionaries representing missing quizzes. If
        quickcheck is true, only the first result is returned.
    """
    quizzes = get_quizzes(course_id)

    missing_list = []

    for canvas_quiz in quizzes:
        quiz = Quiz.query.filter_by(canvas_id=canvas_quiz.get('id')).first()

        if quiz:
            # Already exists. Next!
            continue

        missing_list.append(canvas_quiz)

        if quickcheck:
            # Found one! Quickcheck complete.
            break

    return missing_list
