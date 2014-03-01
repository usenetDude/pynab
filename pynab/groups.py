from pynab import log
from pynab.db import db
from pynab.server import Server
from pynab import parts
import config

MESSAGE_LIMIT = config.scan.get('message_scan_limit', 20000)


def backfill(group_name, date=None):
    log.info('{}: backfilling group'.format(group_name))

    server = Server()
    _, count, first, last, _ = server.group(group_name)

    if date:
        target_article = server.day_to_post(group_name, server.days_old(date))
    else:
        target_article = server.day_to_post(group_name, config.scan.get('backfill_days', 10))

    group = db.groups.find_one({'name': group_name})
    if group:
        # if the group hasn't been updated before, quit
        if not group['first']:
            log.error('{}: run a normal update prior to backfilling'.format(group_name))
            if server.connection:
                server.connection.quit()
            return False

        # if the first article we have is lower than the target
        if target_article >= group['first']:
            log.info('{}: Nothing to do, we already have the target post.'.format(group_name))
            if server.connection:
                server.connection.quit()
            return True

        # or if the target is below the server's first
        if target_article < first:
            target_article = first

        total = group['first'] - target_article
        end = group['first'] - 1
        start = end - MESSAGE_LIMIT + 1
        if target_article > start:
            start = target_article

        retries = 0
        while True:
            messages = server.scan(group_name, start, end)

            if messages:
                if parts.save_all(messages):
                    db.groups.update({
                                         '_id': group['_id']
                                     },
                                     {
                                         '$set': {
                                             'first': start
                                         }
                                     })
                    retries = 0
                else:
                    log.error('{}: failed while saving parts'.format(group_name))
                    if server.connection:
                        server.connection.quit()
                    return False
            else:
                    log.error('{}: problem updating group - trying again'.format(group_name))
                    retries += 1
                    # keep trying the same block 3 times, then skip
                    if retries <= 3:
                        continue

            if start == target_article:
                if server.connection:
                    server.connection.quit()
                return True
            else:
                end = start - 1
                start = end - MESSAGE_LIMIT + 1
                if target_article > start:
                    start = target_article
    else:
        log.error('{}: group doesn\'t exist in db.'.format(group_name))
        if server.connection:
            server.connection.quit()
        return False


def update(group_name):
    log.info('{}: updating group'.format(group_name))

    server = Server()
    _, count, first, last, _ = server.group(group_name)

    group = db.groups.find_one({'name': group_name})
    if group:
        # if the group has been scanned before
        if group['last']:
            # pick up where we left off
            start = group['last'] + 1

            # if our last article is newer than the server's, something's wrong
            if last < group['last']:
                log.error('{}: last article {:d} on server is older than the local {:d}'.format(group_name, last,
                                                                                                group['last']))
                if server.connection:
                    try:
                        server.connection.quit()
                    except:
                        pass
                return False
        else:
            # otherwise, start from x days old
            start = server.day_to_post(group_name, config.scan.get('new_group_scan_days', 5))
            if not start:
                log.error('{}: couldn\'t determine a start point for group'.format(group_name))
                if server.connection:
                    try:
                        server.connection.quit()
                    except:
                        pass
                return False
            else:
                db.groups.update({
                                     '_id': group['_id']
                                 },
                                 {
                                     '$set': {
                                         'first': start
                                     }
                                 })

        # either way, we're going upwards so end is the last available
        end = last

        # if total > 0, we have new parts
        total = end - start + 1

        start_date = server.post_date(group_name, start)
        end_date = server.post_date(group_name, end)

        if start_date and end_date:
            total_date = end_date - start_date

            log.info('{}: pulling {} - {} ({}d, {}h, {}m)'.format(
                group_name,
                start, end,
                total_date.days,
                total_date.seconds // 3600,
                (total_date.seconds // 60) % 60
            ))
        else:
            log.info('{}: pulling {} - {}'.format(group_name, start, end))

        if total > 0:
            if not group['last']:
                log.info('{}: starting new group with {:d} days and {:d} new parts'
                    .format(group_name, config.scan.get('new_group_scan_days', 5), total))
            else:
                log.info('{}: group has {:d} new parts.'.format(group_name, total))

            retries = 0
            # until we're finished, loop
            while True:
                # break the load into segments
                if total > MESSAGE_LIMIT:
                    if start + MESSAGE_LIMIT > last:
                        end = last
                    else:
                        end = start + MESSAGE_LIMIT - 1

                messages = server.scan(group_name, start, end)
                if messages:
                    if parts.save_all(messages):
                        db.groups.update({
                                             '_id': group['_id']
                                         },
                                         {
                                             '$set': {
                                                 'last': end
                                             }
                                         })
                        retries = 0
                    else:
                        log.error('{}: failed while saving parts'.format(group_name))
                        if server.connection:
                            try:
                                server.connection.quit()
                            except:
                                pass
                        return False

                if end == last:
                    if server.connection:
                        try:
                            server.connection.quit()
                        except:
                            pass
                    return True
                else:
                    start = end + 1
        else:
            log.info('{}: no new messages'.format(group_name))
            if server.connection:
                server.connection.quit()
            return True
    else:
        log.error('{}: no group in db'.format(group_name))
        if server.connection:
            server.connection.quit()
        return False