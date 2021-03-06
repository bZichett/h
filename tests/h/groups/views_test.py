# -*- coding: utf-8 -*-
import itertools

import deform
import mock
import pytest
from pyramid import httpexceptions
from pyramid import testing

from h.groups import views


_SENTINEL = object()


def _mock_request(feature=None, settings=None, params=None,
                  authenticated_userid=_SENTINEL, route_url=None, **kwargs):
    """Return a mock Pyramid request object."""
    params = params or {"foo": "bar"}
    if authenticated_userid is _SENTINEL:
        authenticated_userid = "acct:fred@hypothes.is"
    return mock.Mock(
        feature=feature or (lambda feature: True),
        registry=mock.Mock(settings=settings or {}),
        params=params, POST=params,
        authenticated_userid=authenticated_userid,
        route_url=route_url or mock.Mock(return_value="test-read-url"),
        **kwargs)


def _matchdict():
    """Return a matchdict like the one the group_read route receives."""
    return {"pubid": mock.sentinel.pubid, "slug": mock.sentinel.slug}


# The fixtures required to mock all of create_form()'s dependencies.
create_form_fixtures = pytest.mark.usefixtures('GroupSchema', 'Form')


@create_form_fixtures
def test_create_form_creates_form_with_GroupSchema(GroupSchema, Form):
    test_schema = mock.Mock()
    GroupSchema.return_value = mock.Mock(
        bind=mock.Mock(return_value=test_schema))

    views.create_form(request=_mock_request())

    assert Form.call_args[0][0] == test_schema


@create_form_fixtures
def test_create_form_returns_form(Form):
    test_form = mock.Mock()
    Form.return_value = test_form

    result = views.create_form(request=_mock_request())

    assert result["form"] == test_form.render.return_value


# The fixtures required to mock all of create()'s dependencies.
create_fixtures = pytest.mark.usefixtures('GroupSchema', 'Form', 'Group',
                                          'session_model')


@create_fixtures
def test_create_inits_form_with_schema(GroupSchema, Form):
    schema = mock.Mock()
    GroupSchema.return_value = mock.Mock(bind=mock.Mock(return_value=schema))

    views.create(request=_mock_request())

    assert Form.call_args[0][0] == schema


@create_fixtures
def test_create_validates_form(Form):
    Form.return_value = form = mock.Mock()
    form.validate.return_value = {"name": "new group"}
    request = _mock_request()

    views.create(request)

    form.validate.assert_called_once_with(request.params.items())


@create_fixtures
def test_create_rerenders_form_on_validation_failure(Form):
    Form.return_value = form = mock.Mock()
    form.validate.side_effect = deform.ValidationFailure(None, None, None)

    result = views.create(_mock_request())

    assert result['form'] == form.render.return_value


@create_fixtures
def test_create_gets_user_with_authenticated_id(Form):
    """It uses the "name" from the validated data to create a new group."""
    Form.return_value = mock.Mock(validate=lambda data: {"name": "test-group"})
    request = _mock_request()
    type(request).authenticated_user = user_property = mock.PropertyMock()

    views.create(request)

    user_property.assert_called_once_with()


@create_fixtures
def test_create_uses_name_from_validated_data(Form, Group):
    """It uses the "name" from the validated data to create a new group."""
    Form.return_value = mock.Mock(validate=lambda data: {"name": "test-group"})
    request = _mock_request()
    request.authenticated_user = user = mock.Mock()

    views.create(request)

    Group.assert_called_once_with(name="test-group", creator=user)


@create_fixtures
def test_create_adds_group_to_db(Group):
    """It should add the new group to the database session."""
    group = mock.Mock(id=6)
    Group.return_value = group
    request = _mock_request()

    views.create(request)

    request.db.add.assert_called_once_with(group)


@create_fixtures
def test_create_redirects_to_group_read_page(Group):
    """After successfully creating a new group it should redirect."""
    group = mock.Mock(id='test-id', slug='test-slug')
    Group.return_value = group
    request = _mock_request()

    result = views.create(request)

    assert isinstance(result, httpexceptions.HTTPRedirection)


@create_fixtures
def test_create_with_non_ascii_name():
    views.create(_mock_request(params={"name": u"☆ ßüper Gröup ☆"}))


@create_fixtures
def test_create_publishes_join_event(Group, session_model):
    group = mock.Mock(pubid=mock.sentinel.pubid)
    Group.return_value = group
    request = _mock_request()

    views.create(request)

    request.realtime.publish_user.assert_called_once_with({
        'type': 'group-join',
        'userid': request.authenticated_userid,
        'group': group.pubid,
        'session_model': session_model(),
    })


# The fixtures required to mock all of read()'s dependencies.
read_fixtures = pytest.mark.usefixtures('Group',
                                        'renderers',
                                        'routes',
                                        'presenters')


@read_fixtures
def test_read_gets_group_by_pubid(Group):
    request = _mock_request(matchdict={'pubid': 'abc', 'slug': 'snail'})
    views.read(request)

    Group.get_by_pubid.assert_called_once_with(request.db, 'abc')


@read_fixtures
def test_read_404s_when_group_does_not_exist(Group):
    Group.get_by_pubid.return_value = None

    with pytest.raises(httpexceptions.HTTPNotFound):
        views.read(_mock_request(matchdict=_matchdict()))


@read_fixtures
def test_read_without_slug_redirects(Group):
    """/groups/<pubid> should redirect to /groups/<pubid>/<slug>."""
    group = Group.get_by_pubid.return_value = mock.Mock()
    matchdict = {"pubid": "1"}  # No slug.
    request = _mock_request(matchdict=matchdict)

    result = views.read(request)

    assert isinstance(result, httpexceptions.HTTPRedirection)


@read_fixtures
def test_read_with_wrong_slug_redirects(Group):
    """/groups/<pubid>/<wrong> should redirect to /groups/<pubid>/<slug>."""
    group = Group.get_by_pubid.return_value = mock.Mock(slug="my-group")
    matchdict = {"pubid": "1", "slug": "my-gro"}  # Wrong slug.
    request = _mock_request(matchdict=matchdict)

    result = views.read(request)

    assert isinstance(result, httpexceptions.HTTPRedirection)


@read_fixtures
def test_read_if_not_logged_in_renders_share_group_page(Group, renderers):
    """If not logged in should render the "Login to join this group" page."""
    Group.get_by_pubid.return_value = mock.Mock(slug=mock.sentinel.slug)
    request = _mock_request(authenticated_userid=None, matchdict=_matchdict())

    views.read(request)

    assert renderers.render_to_response.call_args[1]['renderer_name'] == (
        'h:templates/groups/join.html.jinja2')


@read_fixtures
def test_read_if_not_logged_in_passes_group(Group, renderers):
    """It should pass the group to the template."""
    g = Group.get_by_pubid.return_value = mock.Mock(slug=mock.sentinel.slug)
    request = _mock_request(authenticated_userid=None, matchdict=_matchdict())

    views.read(request)

    assert renderers.render_to_response.call_args[1]['value']['group'] == g


@read_fixtures
def test_read_if_not_logged_in_returns_response(
        Group, renderers):
    """It should return the response from render_to_response()."""
    Group.get_by_pubid.return_value = mock.Mock(slug=mock.sentinel.slug)
    request = _mock_request(authenticated_userid=None, matchdict=_matchdict())
    renderers.render_to_response.return_value = mock.sentinel.response

    response = views.read(request)

    assert response == mock.sentinel.response


@read_fixtures
def test_read_if_not_a_member_renders_template(Group, renderers):
    """It should render the "Join this group" template."""
    request = _mock_request(matchdict=_matchdict())
    Group.get_by_pubid.return_value = mock.Mock(slug=mock.sentinel.slug)
    user = request.authenticated_user = mock.Mock()
    user.groups = []  # The user isn't a member of the group.

    views.read(request)

    assert renderers.render_to_response.call_args[1]['renderer_name'] == (
        'h:templates/groups/join.html.jinja2')


@read_fixtures
def test_read_if_not_a_member_passes_group_to_template(Group, renderers):
    """It should get the join URL and pass it to the template."""
    request = _mock_request(matchdict=_matchdict())
    g = Group.get_by_pubid.return_value = mock.Mock(slug=mock.sentinel.slug)
    user = request.authenticated_user = mock.Mock()
    user.groups = []  # The user isn't a member of the group.

    views.read(request)

    assert renderers.render_to_response.call_args[1]['value']['group'] == g


@read_fixtures
def test_read_if_not_a_member_passes_join_url_to_template(Group, renderers):
    """It should get the join URL and pass it to the template."""
    request = _mock_request(matchdict=_matchdict())
    request.route_url.return_value = mock.sentinel.join_url
    Group.get_by_pubid.return_value = mock.Mock(slug=mock.sentinel.slug)
    user = request.authenticated_user = mock.Mock()
    user.groups = []  # The user isn't a member of the group.

    views.read(request)

    assert renderers.render_to_response.call_args[1]['value']['join_url'] == (
        mock.sentinel.join_url)


@read_fixtures
def test_read_if_not_a_member_returns_response(Group, renderers):
    """It should return the response from render_to_response()."""
    request = _mock_request(matchdict=_matchdict())
    Group.get_by_pubid.return_value = mock.Mock(slug=mock.sentinel.slug)
    user = request.authenticated_user = mock.Mock()
    user.groups = []  # The user isn't a member of the group.
    renderers.render_to_response.return_value = mock.sentinel.response

    assert views.read(request) == mock.sentinel.response


@read_fixtures
def test_read_if_already_a_member_gets_groups_documents(Group,
                                                        share_group_request):
    views.read(share_group_request)

    Group.get_by_pubid.return_value.documents.assert_called_once_with()


@read_fixtures
def test_read_if_already_a_member_presents_documents(Group,
                                                     share_group_request,
                                                     presenters):
    """It should call DocumentHTMLPresenter with each annotation."""

    document_1 = mock.Mock()
    document_2 = mock.Mock()
    document_3 = mock.Mock()
    Group.get_by_pubid.return_value.documents.return_value = [
        document_1, document_2, document_3]

    views.read(share_group_request)

    presenters.DocumentHTMLPresenter.assert_has_calls(
        [mock.call(document_1), mock.call(document_2), mock.call(document_3)],
        any_order=True
    )


@read_fixtures
def test_read_if_already_a_member_renders_template(share_group_request,
                                                   renderers):
    """It should render the "Share this group" template."""
    views.read(share_group_request)

    assert renderers.render_to_response.call_args[1]['renderer_name'] == (
        'h:templates/groups/share.html.jinja2')


@read_fixtures
def test_read_if_already_a_member_passes_group_to_template(share_group_request,
                                                           Group,
                                                           renderers):
    """It passes the group to the template."""
    views.read(share_group_request)

    assert renderers.render_to_response.call_args[1]['value']['group'] == (
        Group.get_by_pubid.return_value)


@read_fixtures
def test_read_if_already_a_member_gets_group_url(Group, share_group_request):
    share_group_request.route_url = mock.Mock()

    views.read(share_group_request)

    share_group_request.route_url.assert_called_once_with(
        'group_read',
        pubid=Group.get_by_pubid.return_value.pubid,
        slug=Group.get_by_pubid.return_value.slug)


@read_fixtures
def test_read_if_already_a_member_passes_group_url_to_template(
        share_group_request,
        renderers):
    share_group_request.route_url = mock.Mock()

    views.read(share_group_request)

    assert renderers.render_to_response.call_args[1]['value']['group_url'] == (
        share_group_request.route_url.return_value)


@read_fixtures
def test_read_if_already_a_member_passes_document_links_to_template(
        Group,
        share_group_request,
        presenters,
        renderers):
    """It should pass the document links to the template."""
    document_1 = mock.Mock()
    document_2 = mock.Mock()
    document_3 = mock.Mock()
    Group.get_by_pubid.return_value.documents.return_value = [
        document_1, document_2, document_3]

    views.read(share_group_request)

    assert renderers.render_to_response.call_args[1]['value']['document_links'] == [
        'document_link_1', 'document_link_2', 'document_link_3']


@read_fixtures
def test_read_if_already_a_member_when_group_has_no_annotated_documents(
        Group,
        share_group_request,
        presenters,
        renderers):
    Group.get_by_pubid.return_value.documents.return_value = []

    views.read(share_group_request)

    assert not presenters.DocumentHTMLPresenter.called
    assert renderers.render_to_response.call_args[1]['value']['document_links'] == []


@read_fixtures
def test_read_if_already_a_member_returns_response(share_group_request,
                                                   renderers):
    """It should return the response from render_to_response()."""
    renderers.render_to_response.return_value = mock.sentinel.response

    assert views.read(share_group_request) == mock.sentinel.response


# The fixtures required to mock all of join()'s dependencies.
join_fixtures = pytest.mark.usefixtures('Group', 'session_model')


@join_fixtures
def test_join_gets_group_by_pubid(Group):
    request = _mock_request(matchdict={'pubid': 'twibble', 'slug': 'snail'})
    views.join(request)

    Group.get_by_pubid.assert_called_once_with(request.db, "twibble")


@join_fixtures
def test_join_404s_if_group_not_found(Group):
    Group.get_by_pubid.return_value = None

    with pytest.raises(httpexceptions.HTTPNotFound):
        views.join(_mock_request(matchdict=_matchdict()))


@join_fixtures
def test_join_gets_user():
    request = _mock_request(matchdict=_matchdict())
    type(request).authenticated_user = user_property = mock.PropertyMock()

    views.join(request)

    user_property.assert_called_once_with()


@join_fixtures
def test_join_adds_user_to_group_members(Group):
    Group.get_by_pubid.return_value = group = mock.Mock()
    request = _mock_request(
        matchdict=_matchdict(), authenticated_user=mock.sentinel.user)

    views.join(request)

    group.members.append.assert_called_once_with(mock.sentinel.user)


@join_fixtures
def test_join_redirects_to_group_page(Group):
    slug = "test-slug"
    group = Group.get_by_pubid.return_value = mock.Mock(slug=slug)
    request = _mock_request(matchdict=_matchdict())

    result = views.join(request)

    assert isinstance(result, httpexceptions.HTTPRedirection)


@join_fixtures
def test_join_publishes_join_event(Group, session_model):
    group = mock.Mock(pubid = mock.sentinel.pubid)
    Group.get_by_pubid.return_value = group
    request = _mock_request(matchdict=_matchdict())

    views.join(request)

    request.realtime.publish_user.assert_called_once_with({
        'type': 'group-join',
        'userid': request.authenticated_userid,
        'group': mock.sentinel.pubid,
        'session_model': session_model(),
    })


leave_fixtures = pytest.mark.usefixtures('Group', 'session_model')


@leave_fixtures
def test_leave_removes_user_from_group_members(Group):
    user = mock.sentinel.user
    group = mock.Mock()
    group.members = [user]
    Group.get_by_pubid.return_value = group
    request = _mock_request(
        matchdict=_matchdict(), authenticated_user=user)

    result = views.leave(request)

    assert(group.members == [])


@leave_fixtures
def test_leave_returns_not_found_if_user_not_in_group(Group):
    group = mock.Mock(members=[])
    Group.get_by_pubid.return_value = group
    request = _mock_request(matchdict=_matchdict(), user=mock.sentinel.user)

    with pytest.raises(httpexceptions.HTTPNotFound):
        result = views.leave(request)


@leave_fixtures
def test_leave_publishes_leave_event(Group, session_model):
    group = mock.Mock(pubid=mock.sentinel.pubid,
                      members=[mock.sentinel.user])
    Group.get_by_pubid.return_value = group
    request = _mock_request(
        matchdict=_matchdict(), authenticated_user=mock.sentinel.user)

    views.leave(request)

    request.realtime.publish_user.assert_called_once_with({
        'type': 'group-leave',
        'userid': request.authenticated_userid,
        'group': mock.sentinel.pubid,
        'session_model': session_model(),
    })


@pytest.fixture
def share_group_request(Group, config, pubid=u'__world__'):
    """
    Return a logged-in, already-member request for the group read page.

    The user is logged-in and is a member of the group.

    """
    request = testing.DummyRequest(db=mock.sentinel.db_session)
    request.matchdict.update({'pubid': pubid, 'slug': 'slug'})

    # The user is logged-in.
    config.testing_securitypolicy('userid')
    request.authenticated_user = mock.Mock()

    # The user is a member of the group.
    request.authenticated_user.groups = [Group.get_by_pubid.return_value]

    return request


@pytest.fixture
def Form(patch):
    return patch('h.groups.views.deform.Form')


@pytest.fixture
def GroupSchema(patch):
    return patch('h.groups.views.schemas.GroupSchema')


@pytest.fixture
def Group(patch):
    Group = patch('h.groups.views.models.Group')
    Group.get_by_pubid.return_value = mock.Mock(slug='slug', pubid=u'xyz123')
    Group.get_by_pubid.return_value.documents.return_value = []
    return Group


@pytest.fixture
def session_model(patch):
    return patch('h.session.model', autospec=False)


@pytest.fixture
def renderers(patch):
    return patch('h.groups.views.renderers')


@pytest.fixture
def routes(config):
    config.add_route('group_read', '/groups/{pubid}/{slug:[^/]*}')


@pytest.fixture
def presenters(patch):
    presenters = patch('h.groups.views.presenters')

    # The first call to DocumentHTMLPresenter() returns
    # mock.Mock(link='document_link_1'), the second returns
    # mock.Mock(link='document_link_2'), and so on.
    presenters.DocumentHTMLPresenter.side_effect = itertools.imap(
        lambda i: mock.Mock(link="document_link_" + str(i)),
        itertools.count(start=1))

    return presenters
