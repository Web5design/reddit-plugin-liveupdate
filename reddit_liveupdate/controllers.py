from pylons import c
from pylons.i18n import _

from r2.controllers import add_controller
from r2.controllers.reddit_base import RedditController, base_listing
from r2.lib.db import tdb_cassandra
from r2.lib.validator import (
    validate,
    validatedForm,
    VByName,
    VCount,
    VExistingUname,
    VLength,
    VLimit,
    VMarkdown,
    VModhash,
)
from r2.models import QueryBuilder, Account, LinkListing
from r2.lib.errors import errors
from r2.lib.utils import url_links_builder

from reddit_liveupdate import pages
from reddit_liveupdate.models import (
    LiveUpdate,
    LiveUpdateEvent,
    LiveUpdateStream,
)
from reddit_liveupdate.validators import (
    VLiveUpdate,
    VLiveUpdateEventEditor,
    VLiveUpdateEventManager,
    VLiveUpdateID,
    VTimeZone,
)


class LiveUpdateBuilder(QueryBuilder):
    def wrap_items(self, items):
        wrapped = []
        for item in items:
            w = self.wrap(item)
            wrapped.append(w)
        pages.liveupdate_add_props(c.user, wrapped)
        return wrapped

    def keep_item(self, item):
        return not item.deleted


@add_controller
class LiveUpdateController(RedditController):
    def __before__(self, event):
        RedditController.__before__(self)

        if event:
            try:
                c.liveupdate_event = LiveUpdateEvent._byID(event)
            except tdb_cassandra.NotFound:
                pass

        if not c.liveupdate_event:
            self.abort404()

        c.liveupdate_can_manage = (c.liveupdate_event.state == "live" and
                                   (c.user_is_loggedin and c.user_is_admin))
        c.liveupdate_can_edit = (c.liveupdate_event.state == "live" and
                                 (c.user_is_loggedin and
                                  (c.liveupdate_event.is_editor(c.user) or
                                   c.user_is_admin)))

    @validate(
        num=VLimit("limit", default=25, max_limit=100),
        after=VLiveUpdateID("after"),
        before=VLiveUpdateID("before"),
        count=VCount("count"),
    )
    def GET_listing(self, num, after, before, count):
        reverse = False
        if before:
            reverse = True
            after = before

        query = LiveUpdateStream.query([c.liveupdate_event._id],
                                       count=num, reverse=reverse)
        if after:
            query.column_start = after

        builder = LiveUpdateBuilder(query=query, skip=True,
                                    reverse=reverse, num=num,
                                    count=count)
        listing = pages.LiveUpdateListing(builder)
        content = pages.LiveUpdateEvent(
            event=c.liveupdate_event,
            listing=listing.listing(),
        )

        return pages.LiveUpdatePage(
            content=content,
        ).render()

    @base_listing
    def GET_discussions(self, num, after, reverse, count):
        builder = url_links_builder(
            url="/live/" + c.liveupdate_event._id,
            num=num,
            after=after,
            reverse=reverse,
            count=count,
        )
        listing = LinkListing(builder).listing()
        return pages.LiveUpdatePage(
            content=listing,
        ).render()

    @validate(
        VLiveUpdateEventEditor(),
    )
    def GET_edit(self):
        return pages.LiveUpdatePage(
            content=pages.LiveUpdateEventConfiguration(),
        ).render()

    @validatedForm(
        VLiveUpdateEventEditor(),
        VModhash(),
        title=VLength("title", max_length=120),
        description=VMarkdown("description", empty_error=None),
        timezone=VTimeZone("timezone"),
    )
    def POST_edit(self, form, jquery, title, description, timezone):
        if form.has_errors("title", errors.NO_TEXT,
                                    errors.TOO_LONG):
            return

        if form.has_errors("description", errors.TOO_LONG):
            return

        if form.has_errors("timezone", errors.INVALID_TIMEZONE):
            return

        c.liveupdate_event.title = title
        c.liveupdate_event.description = description
        c.liveupdate_event.timezone = timezone.zone
        c.liveupdate_event._commit()

        form.set_html(".status", _("saved"))
        form.refresh()

    @validate(
        VLiveUpdateEventManager(),
    )
    def GET_editors(self):
        return pages.LiveUpdatePage(
            content=pages.EditorList(c.liveupdate_event),
        ).render()

    @validatedForm(
        VLiveUpdateEventManager(),
        VModhash(),
        user=VExistingUname("name"),
    )
    def POST_add_editor(self, form, jquery, user):
        if form.has_errors("name", errors.USER_DOESNT_EXIST,
                                   errors.NO_USER):
            return

        # make the user able to edit
        c.liveupdate_event.add_editor(user)

        # TODO: send PM to new editor

        # add the user to the table
        user_row = (pages.EditorList(c.liveupdate_event)
                         .user_row(pages.EditorList.type, user))
        jquery(".liveupdate_editor-table").show(
            ).find("table").insert_table_rows(user_row)

    @validatedForm(
        VLiveUpdateEventManager(),
        VModhash(),
        user=VByName("id", thing_cls=Account),
    )
    def POST_rm_editor(self, form, jquery, user):
        c.liveupdate_event.remove_editor(user)

    @validatedForm(
        VLiveUpdateEventEditor(),
        VModhash(),
        text=VMarkdown("body", max_length=512),
    )
    def POST_update(self, form, jquery, text):
        if form.has_errors("body", errors.NO_TEXT,
                                   errors.TOO_LONG):
            return

        # create and store the new update
        update = LiveUpdate(data={
            "author_id": c.user._id,
            "body": text,
        })
        LiveUpdateStream.add_update(c.liveupdate_event, update)

        # send back a rendered update for client-side insertion
        builder = LiveUpdateBuilder(None)
        wrapped = builder.wrap_items([update])
        jquery._things(wrapped, "insert_liveupdates")

        # reset the submission form
        t = form.find("textarea")
        t.attr('rows', 3).html("").val("")

    @validatedForm(
        VLiveUpdateEventEditor(),
        VModhash(),
        update=VLiveUpdate("id"),
    )
    def POST_delete_update(self, form, jquery, update):
        if form.has_errors("id", errors.NO_THING_ID):
            return

        update.deleted = True
        LiveUpdateStream.add_update(c.liveupdate_event, update)

    @validatedForm(
        VLiveUpdateEventEditor(),
        VModhash(),
        update=VLiveUpdate("id"),
    )
    def POST_strike_update(self, form, jquery, update):
        if form.has_errors("id", errors.NO_THING_ID):
            return

        update.stricken = True
        LiveUpdateStream.add_update(c.liveupdate_event, update)