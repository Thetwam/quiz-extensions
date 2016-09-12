from flask import Flask, render_template, session, request, redirect, url_for
from functools import wraps

from collections import defaultdict
import requests
import json
import math

# OAuth specific
from ims_lti_py import ToolProvider
from time import time

from models import db, Course, Extension, Quiz, User
from utils import get_quizzes, search_users, get_user, get_course, get_or_create
import config

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = config.SQLALCHEMY_DATABASE_URI

db.init_app(app)

oauth_creds = {config.LTI_KEY: config.LTI_SECRET}

json_headers = {'Authorization': 'Bearer ' + config.API_KEY, 'Content-type': 'application/json'}


def check_valid_user(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        """
        Decorator to check if the user is allowed access to the app.

        If user is allowed, return the decorated function.
        Otherwise, return an error page with corresponding message.
        """
        canvas_user_id = session.get('canvas_user_id')
        if not session.get('lti_logged_in') or not canvas_user_id:
            return render_template(
                'error.html',
                message='Not allowed!'
            )
        if 'course_id' not in kwargs.keys():
            return render_template(
                'error.html',
                message='No course_id provided.'
            )
        course_id = int(kwargs.get('course_id'))

        if not session['is_admin']:
            enrollments_url = "%scourses/%s/enrollments" % (config.API_URL, course_id)

            payload = {
                'user_id': canvas_user_id,
                'type': ['TeacherEnrollment', 'TaEnrollment', 'DesignerEnrollment']
            }

            user_enrollments_response = requests.get(
                enrollments_url,
                data=json.dumps(payload),
                headers=json_headers
            )
            user_enrollments = user_enrollments_response.json()

            if not user_enrollments or 'errors' in user_enrollments:
                return render_template(
                    'error.html',
                    message='You are not enrolled in this course as a Teacher, TA, or Designer.'
                )

        return f(*args, **kwargs)
    return decorated_function


@app.route("/", methods=['POST', 'GET'])
def index():
    """
    Default app index.
    """
    return "Please contact your System Administrator."


@app.route("/xml/", methods=['POST', 'GET'])
def xml():
    """
    Returns the lti.xml file for the app.
    """
    return render_template(
        'lti.xml',
        tool_id=config.LTI_TOOL_ID,
        domain=config.LTI_DOMAIN,
        launch_url=config.LTI_LAUNCH_URL
    )


@app.route("/quiz/<course_id>", methods=['GET'])
@check_valid_user
def quiz(course_id=None):
    """
    Main landing page for the app.

    Displays a page to the user that allows them to select students
    to moderate quizzes for.
    """
    if not course_id:
        return render_template(
            'error.html',
            message='course_id required',
        )

    return render_template(
        'userselect.html',
        course_id=course_id,
        current_page_number=1
    )


@app.route("/update/<course_id>/", methods=['POST'])
@check_valid_user
def update(course_id=None):
    """
    Processes requests to update time on selected students' quizzes to
    a specified percentage.

    Accepts a JSON formatted object that includes the percent of time
    and a list of canvas user ids.

    Example:
    {
        "percent": "300",
        "user_ids": [
            "0123456",
            "1234567",
            "9867543",
            "5555555"
        ]
    }
    """
    if not course_id:
        return "course_id required"

    post_json = request.get_json()

    if not post_json:
        return "invalid request"

    course_json = get_course(course_id)
    course_name = course_json.get('name', '<UNNAMED COURSE>')

    # add/update course in db
    course = Course.query.filter_by(canvas_id=course_id).first()
    if course is None:
        course = Course(course_id, course_name)
        db.session.add(course)
    else:
        course.canvas_id = course_id
        course.course_name = course_name

    db.session.commit()

    user_ids = post_json.get('user_ids', [])
    percent = post_json.get('percent', None)

    if not percent:
        return "percent required"

    # add/update users in db
    for user_id in user_ids:
        user = User.query.filter_by(canvas_id=user_id).first()

        try:
            canvas_user = get_user(user_id)

            sortable_name = canvas_user.get('sortable_name', '<MISSING NAME>')
            canvas_user_id = canvas_user.get('id')
            sis_id = canvas_user.get('sis_user_id')

        except requests.exceptions.HTTPError:
            # unable to find user
            continue

        if user is None:
            # create
            user = User(sortable_name, canvas_user_id, sis_id)
            db.session.add(user)
        else:
            # update
            user.sortable_name = sortable_name
            user.canvas_id = canvas_user_id
            user.sis_id = sis_id

        # create/update extension
        extension = Extension.query.filter_by(course_id=course_id, user_id=user_id).first()
        if extension is None:
            extension = Extension(course_id, user_id, percent)
            db.session.add(extension)
        else:
            extension.percent = percent

        db.session.commit()

    quizzes = get_quizzes(course_id)
    num_quizzes = len(quizzes)
    quiz_time_list = []
    unchanged_quiz_time_list = []

    if num_quizzes < 1:
        return json.dumps({
            "error": True,
            "message": "Sorry, there are no quizzes for this course."
        })

    for quiz in quizzes:
        quiz_id = quiz.get('id', None)
        quiz_title = quiz.get('title', "[UNTITLED QUIZ]")

        # add/update quiz
        quiz_obj = Quiz.query.filter_by(canvas_id=quiz_id).first()
        if quiz_obj is None:
            quiz_obj = Quiz(quiz_id, course_id, quiz_title)
            db.session.add(quiz_obj)
        else:
            quiz_obj.canvas_id = quiz_id
            quiz_obj.course_id = course_id
            quiz_obj.title = quiz_title

        db.session.commit()

        extension_response = extend_quiz(course_id, quiz, percent, user_ids)

        if extension_response.get('success', False) is True:
            added_time = extension_response.get('added_time', None)
            if added_time is not None:
                quiz_time_list.append({
                    "title": quiz_title,
                    "added_time": added_time
                })
            else:
                unchanged_quiz_time_list.append({"title": quiz_title})
        else:
            return json.dumps({
                'error': True,
                'message': extension_response.get('message', 'An unknown error occured')
            })

    msg_str = (
        'Success! {} {} been updated for {} student(s) to have {}% time. '
        '{} {} no time limit and were left unchanged.'
    )

    message = msg_str.format(
        len(quiz_time_list),
        "quizzes have" if len(quiz_time_list) != 1 else "quiz has",
        len(user_ids),
        percent,
        len(unchanged_quiz_time_list),
        "quizzes have" if len(unchanged_quiz_time_list) != 1 else "quiz has"
    )

    return json.dumps({
        "error": False,
        "message": message,
        "quiz_list": quiz_time_list,
        "unchanged_list": unchanged_quiz_time_list
    })


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


@app.route("/refresh/<course_id>/2", methods=['POST'])
def refresh(course_id):
    """
    Look up existing extensions and apply them to new quizzes.
    """
    course = Course.query.filter_by(canvas_id=course_id).first()

    if course is None:
        return "Course not found"

    percent_user_map = defaultdict(list)
    for extension in course.extensions:
        percent_user_map[extension.percent].append(extension.user_id)

    # quiz stuff
    quizzes = missing_quizzes(course_id)

    for quiz in quizzes:
        for percent, user_list in percent_user_map.iteritems():
            extend_quiz(course_id, quiz, percent, user_list)


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
        quiz = Quiz.query.filter_by(canvas_id=canvas_quiz.id).first()

        if quiz:
            # Already exists. Next!
            continue

        missing_list.append(canvas_quiz)

        if quickcheck:
            # Found one! Quickcheck complete.
            break

    return missing_list


@app.route("/filter/<course_id>/", methods=['GET'])
@check_valid_user
def filter(course_id=None):
    """
    Displays a filtered and paginated list of students in the course.
    """
    if not course_id:
        return "course_id required"

    query = request.args.get('query', '').lower()
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', config.DEFAULT_PER_PAGE))

    user_list, max_pages = search_users(
        course_id,
        per_page=per_page,
        page=page,
        search_term=query
    )

    if not user_list or max_pages < 1:
        user_list = []
        max_pages = 1

    return render_template(
        'user_list.html',
        users=user_list,
        current_page_number=page,
        max_pages=max_pages
    )


@app.route('/launch', methods=['POST'])
def lti_tool():
    """
    Bootstrapper for lti.
    """
    course_id = request.form.get('custom_canvas_course_id')
    canvas_user_id = request.form.get('custom_canvas_user_id')

    roles = request.form['ext_roles']
    if "Administrator" not in roles and "Instructor" not in roles:
        return render_template(
            'error.html',
            message='Must be an Administrator or Instructor',
            params=request.form
        )

    session["is_admin"] = "Administrator" in roles

    key = request.form.get('oauth_consumer_key')
    if key:
        secret = oauth_creds.get(key)
        if secret:
            tool_provider = ToolProvider(key, secret, request.form)
        else:
            tool_provider = ToolProvider(None, None, request.form)
            tool_provider.lti_msg = 'Your consumer didn\'t use a recognized key'
            tool_provider.lti_errorlog = 'You did it wrong!'
            return render_template(
                'error.html',
                message='Consumer key wasn\'t recognized',
                params=request.form
            )
    else:
        return render_template('error.html', message='No consumer key')

    if not tool_provider.is_valid_request(request):
        return render_template(
            'error.html',
            message='The OAuth signature was invalid',
            params=request.form
        )

    if time() - int(tool_provider.oauth_timestamp) > 60*60:
        return render_template('error.html', message='Your request is too old.')

    # This does truly check anything, it's just here to remind you  that real
    # tools should be checking the OAuth nonce
    if was_nonce_used_in_last_x_minutes(tool_provider.oauth_nonce, 60):
        return render_template('error.html', message='Why are you reusing the nonce?')

    session['canvas_user_id'] = canvas_user_id
    session['lti_logged_in'] = True

    session['launch_params'] = tool_provider.to_params()
    username = tool_provider.username('Dude')

    if tool_provider.is_outcome_service():
        return render_template('assessment.html', username=username)
    else:
        return redirect(url_for('quiz', course_id=course_id))


def was_nonce_used_in_last_x_minutes(nonce, minutes):
    return False


@app.before_first_request
def create_db():
    db.create_all()

if __name__ == "__main__":
    app.debug = config.DEBUG
    app.secret_key = config.SECRET_KEY
    app.run(host=config.HOST, ssl_context=config.SSL_CONTEXT, port=config.PORT)
