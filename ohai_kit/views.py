try:
    from urllib.parse import urlparse
except ImportError:     # Python 2
    from urlparse import urlparse

from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render, get_object_or_404, resolve_url
from django.conf import settings
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.contrib.auth.decorators import user_passes_test, login_required
from django.contrib.auth.models import User
from django.contrib.auth.views import redirect_to_login, login as __login
from django.core.urlresolvers import reverse
from django.utils import timezone
from django.utils.encoding import force_str

from ohai_kit.models import Project, JobInstance, \
    WorkStep, WorkReceipt


def get_active_jobs(user, project=None):
    """
    Returns a list of active jobs for the current user, and optionally
    for a given project.  If the project is specified, then the return
    will be either a job or False.  If the project is not specified,
    the return will be a list.
    """
    querie = {
        "user" : user,
        "completion_time" : None,
        }
    if project is not None:
        querie["project"] = project
    found = JobInstance.objects.filter(**querie)
    if project is not None:
        assert len(found) <= 1
        if found:
            return found[0]
        else:
            return False
    return found


def trigger_login_redirect(request, redirect_field_name=REDIRECT_FIELD_NAME, login_url=None):
    """
    Code pulled from the auth module, because its nested, preventing reuse.
    """
    path = request.build_absolute_uri()
    # urlparse chokes on lazy objects in Python 3, force to str
    resolved_login_url = force_str(
        resolve_url(login_url or settings.LOGIN_URL))
    # If the login url is the same scheme and net location then just
    # use the path as the "next" url.
    login_scheme, login_netloc = urlparse(resolved_login_url)[:2]
    current_scheme, current_netloc = urlparse(path)[:2]
    if ((not login_scheme or login_scheme == current_scheme) and
        (not login_netloc or login_netloc == current_netloc)):
        path = request.get_full_path()
    return redirect_to_login(
        path, resolved_login_url, redirect_field_name)


def guest_only(view_function, redirect_field_name=REDIRECT_FIELD_NAME, login_url=None):
    """
    Replacement for @login_required decorator, so that only guests may
    access the wrapped view.  In this event, the user is probably
    "lost" so instead of redirecting to the login page, just redirect
    to "/".\
    """
    def wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated() and \
           request.session.has_key("bypass_login"):
            return view_function(request, *args, **kwargs)
        else:
            # redirect to login page
            return HttpResponseRedirect("/")
    return wrapped_view


def controlled_view(view_function, redirect_field_name=REDIRECT_FIELD_NAME, login_url=None):
    """
    Replacement for the @login_required decorator that also takes in
    account for if the session is an anonymous one.
    """    
    def wrapped_view(request, *args, **kwargs):
        if request.user.is_authenticated() or \
           request.session.has_key("bypass_login"):
            return view_function(request, *args, **kwargs)
        else:
            # redirect to login page
            return trigger_login_redirect(request, redirect_field_name, login_url)    
    return wrapped_view


def guest_access(request, redirect_field_name=REDIRECT_FIELD_NAME):
    """
    This view sets up a guest session and then redirects to wherever.
    The session is set to expire when the browser closes, prompting
    the login page next time they visit the site.
    """
    request.session.set_expiry(0)
    request.session["bypass_login"] = True
    request.session["touch_emulation"] = False
    return HttpResponseRedirect(request.POST[redirect_field_name])


def worker_access(request, *args, **kargs):
    """
    Wraps django.contrib.auth.views.login, so that some default
    session values can be added for workrs.
    """
    response = __login(request, *args, **kargs)
    if request.user.is_authenticated():
        request.session["touch_emulation"] = True
    return response


def session_settings(request):
    """This view serves the page to override the default session
    variables.  Currently, that means enabling/disabling touch screen
    emulation.  If this view is called as a post, it will update the
    settings and redirect to the dashboard."""

    if request.POST:
        request.session["touch_emulation"] = request.POST.has_key("touch_emulation")
        return HttpResponseRedirect(reverse("ohai_kit:index"))
    else:
        context = {
            "touch_emulation" : False,
            "user" : request.user,
            "is_guest" : request.session.has_key("bypass_login"),
            "touch_setting" : request.session["touch_emulation"],
        }
        return render(request, "ohai_kit/session_settings.html", context)


@controlled_view
def system_index(request):
    """
    The dashboard view is the hub where the user is able to access
    tasks relating to their work, in regards to interacting with
    Projects in the system.  Additionally, administrators might see
    employee stats here at some point.
    """
    projects = Project.objects.all().order_by("name")
    context = {
        "projects" : projects,
        "user" : request.user,
        "is_guest" : request.session.has_key("bypass_login"),
        "touch_emulation" : request.session["touch_emulation"],
        }
    return render(request, "ohai_kit/dashboard.html", context)


@controlled_view
def project_view(request, project_id):
    """
    The Project view does different things for different people.  An
    administrator (eventually) should be able to edit Projects via the
    project view rather than the admin page.  For regular users, this
    will be where they begin a workflow.  If the user is a guest user,
    this should redirect right to the workflow view without a bound job.
    """

    if not request.user.is_authenticated():
        return HttpResponseRedirect(
            reverse("ohai_kit:guest_workflow", args=(project_id,)))


    user = request.user
    project = get_object_or_404(Project, pk=project_id)
    active_job = get_active_jobs(user, project)

    if active_job:
        return HttpResponseRedirect(
            reverse("ohai_kit:job_status", args=(active_job.id,)))
    
    context = {
        "user" : user,
        "project" : project,
        "touch_emulation" : False,
        }

    return render(request, "ohai_kit/project_detail.html", context)


@guest_only
def guest_workflow(request, project_id):
    """
    This view should facilitate the project workflow for guests.
    """
    project = get_object_or_404(Project, pk=project_id)
    sequence = [[step, "pending"] for step in
                project.workstep_set.order_by("sequence_number")]
    if len(sequence) > 0:
        # make the first step "active"
        sequence[0][1] = "active"
    context = {
        "user": request.user,
        "project": project,
        "is_guest" : True,
        "job_id": "-1",
        "sequence": sequence,
        "touch_emulation" : request.session["touch_emulation"],
    }
    return render(request, "ohai_kit/workflow.html", context)


@login_required
def job_status(request, job_id):
    """
    This view should facilitate the project workflow for the current
    job.
    """
    job = get_object_or_404(JobInstance, pk=job_id)
    # FIXME assert that the current user is either staff or the user listed
    # on the job
    assert request.user == job.user
    
    context = {
        "user": request.user,
        "project": job.project,
        "is_guest" : False,
        "job_id": job.pk,
        "sequence": job.get_work_sequence(),
        "touch_emulation" : request.session["touch_emulation"],
    }
    return render(request, "ohai_kit/workflow.html", context)


@login_required
def start_job(request, project_id):
    """
    The user is redirected here to initiate a new JobInstance.
    """
    project = get_object_or_404(Project, pk=project_id)
    job = get_active_jobs(request.user, project)
    if not job:
        job = JobInstance()
        job.project = project
        job.user = request.user
        job.start_time = timezone.now()
        job.batch = "unknown"
        job.save()

    return HttpResponseRedirect(
        reverse("ohai_kit:job_status", args=(job.id,)))


@login_required
def close_job(request, job_id):
    """
    When the user has completed all of the step checks for a given
    job, they should be shown a "close job" button that redirects
    here.
    """
    job = get_object_or_404(JobInstance, pk=job_id)
    # FIXME assert that the current user is either staff or the user listed
    # on the job
    assert request.user == job.user
    if job.completed() and not job.completion_time:
        job.completion_time = timezone.now()
        job.save()

    if job.completed():
        return HttpResponseRedirect(reverse("ohai_kit:index"))
    else:
        return HttpResponseRedirect(
            reverse("ohai_kit:job_status", args=(job_id,)))


@login_required
def update_job(request, job_id):
    """
    The tracker is not to be interacted with via humans, but
    rather is called via XHR from javascript to note job progress
    """
    job = JobInstance.objects.get(id=job_id)
    work_step = WorkStep.objects.get(id=int(request.POST["step_id"]))
    try:
        # if this works, its probably an error, so just ignore it
        receipt = WorkReceipt.objects.get(job=job, step=work_step)
    except WorkReceipt.DoesNotExist:
        # otherwise, create the receipt!
        receipt = WorkReceipt()
        receipt.job = job
        receipt.step = work_step
        receipt.completion_time = timezone.now()
        receipt.save()

    return HttpResponse("OK", content_type="text/plain")
