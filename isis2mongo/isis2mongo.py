# coding: utf-8

import argparse
import subprocess
import logging
import logging.config
import os
from datetime import datetime
import uuid
import json
import re

from articlemeta.client import ThriftClient
from articlemeta.client import UnauthorizedAccess
from articlemeta.client import ServerError

from controller import DataBroker, IsisDataBroker

logger = logging.getLogger(__name__)

DATABASES = (
    ('title', 'journals'),
    ('issue', 'issues'),
    ('artigo', 'articles'),
    ('bib4cit', 'references'),
)

ADMINTOKEN = os.environ.get('ARTICLEMETA_ADMINTOKEN', 'admin')
ARTICLEMETA_THRIFTSERVER = os.environ.get('ARTICLEMETA_THRIFTSERVER', 'admin')
ISO_PATH = os.environ.get('ISO_PATH', os.path.dirname(os.path.abspath(__file__)))
SENTRY_HANDLER = os.environ.get('SENTRY_HANDLER', None)
LOGGING_LEVEL = os.environ.get('LOGGING_LEVEL', None)
LOGGING = {
    'version': 1,
    'disable_existing_loggers': True,

    'formatters': {
        'console': {
            'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            'datefmt': '%H:%M:%S',
            },
        },
    'handlers': {
        'console': {
            'level': LOGGING_LEVEL,
            'class': 'logging.StreamHandler',
            'formatter': 'console'
            }
        },
    'loggers': {
        '': {
            'handlers': ['console'],
            'level': LOGGING_LEVEL,
            'propagate': False,
            },
        'isis2mongo.isis2mongo': {
            'level': LOGGING_LEVEL,
            'propagate': True,
        },
    }
}

if SENTRY_HANDLER:
    LOGGING['handlers']['sentry'] = {
        'level': 'ERROR',
        'class': 'raven.handlers.logging.SentryHandler',
        'dsn': SENTRY_HANDLER,
    }
    LOGGING['loggers']['']['handlers'].append('sentry')


REGEX_FIXISSUEID = re.compile(r'^[0-9]*')


def issue_pid(record):
    """
    This method returns the ISSUE PID according to values registered in
    v35 and v36.
    input: v35: 0032-281X v36: 20023
    output: 0032-281X20020003

    input: v35: 0032-281X v36: 200221
    output: 0032-281X20020021

    input: v35: 0032-281X v36: 20021-4
    output: 0032-281X20020001

    input: v35: 0032-281X v36: 2002
    output: 0032-281X20020000
    """

    try:
        issn = record.get('v35', [{'_': None}])[0]['_']
        publication_year = record.get('v36', [{'_': None}])[0]['_'][0:4]
        issue_order = REGEX_FIXISSUEID.match(record.get('v36', [{'_': None}])[0]['_'][4:]).group() or 0
        order = "%04d" % int(issue_order)
    except TypeError:
        return None

    pid = issn+publication_year+order

    return pid


def load_isis_records(collection, issns=None):

    def prepare_record(collection, record):
        for tag in tuple(record):  # iterate over a fixed sequence of tags
            if str(tag).isdigit():
                record['v'+tag] = record[tag]
                del record[tag]  # this is why we iterate over a tuple
                # with the tags, and not directly on the record dict

        pid = record.get('v880', [{'_': None}])[0]['_'] or issue_pid(record) or record.get('v400', [{'_': None}])[0]['_']

        if not pid:
            return None

        record['v992'] = [{'_': collection}]
        record['collection'] = collection
        record['code'] = pid
        record['v880'] = [{'_': pid}]  # rewriting pid in case the v880 do not exists in record.
        processing_date = record.get('v91', [{'_': datetime.now().isoformat()[:10]}])[0]['_'].replace('-', '') or datetime.now().isoformat()[:10].replace('-', '')
        record['processing_date'] = datetime.strptime(processing_date, '%Y%m%d').isoformat()[:10]
        if len(pid) == 9:
            record['journal'] = pid

        if len(pid) == 17:
            record['journal'] = pid[:9]
            record['issue'] = pid

        if len(pid) == 23:
            record['journal'] = pid[1:10]
            record['issue'] = pid[1:18]
            record['document'] = pid

        if len(pid) == 28:
            record['journal'] = pid[1:10]
            record['issue'] = pid[1:18]
            record['document'] = pid[:23]

        return record

    for iso, coll in DATABASES:
        logger.info('Recording (%s) records for collection (%s)', coll, collection)
        isofile = '%s/../isos/%s/%s.iso' % (ISO_PATH, collection, iso)

        try:
            isis_db = IsisDataBroker(isofile)
        except IOError:
            raise ValueError('ISO file do not exists for the collection (%s), check the collection acronym or the path to the ISO file (%s)' % (collection, isofile))

        for ndx, record in enumerate(isis_db.read()):
            ndx += 1
            logger.debug('Reading record (%d) from iso (%s)', ndx, isofile)

            try:
                record = prepare_record(collection, record)
            except:
                import pdb; pdb.set_trace()
                record = prepare_record(collection, record)
                logger.error('Fail to load document. Integrity error.')
                continue

            if not record:
                continue

            if issns and not record['journal'] in issns:
                continue

            yield (coll, record)


def load_articlemeta_issues_ids(collection, issns=None):
    rc = ThriftClient(domain=ARTICLEMETA_THRIFTSERVER, admintoken=ADMINTOKEN)

    issues_pids = []
    logger.info('Loading articlemeta issues ids')
    for issn in issns or [None]:
        for issue in rc.issues(collection, issn=issn, only_identifiers=True):
            logger.debug(
                'Loading articlemeta issue id (%s)',
                '_'.join([issue.collection, issue.code, issue.processing_date.replace('-', '')])
            )
            issues_pids.append('_'.join([issue.collection, issue.code, issue.processing_date.replace('-', '')]))

    return issues_pids


def load_articlemeta_documents_ids(collection, issns=None):
    rc = ThriftClient(domain=ARTICLEMETA_THRIFTSERVER, admintoken=ADMINTOKEN)

    documents_pids = []
    logger.info('Loading articlemeta documents ids')
    for issn in issns or [None]:
        for document in rc.documents(collection, issn=issn, only_identifiers=True):
            logger.debug(
                'Loading articlemeta document id (%s)',
                '_'.join([document.collection, document.code, document.processing_date.replace('-', '')])
            )
            documents_pids.append('_'.join([document.collection, document.code, document.processing_date.replace('-', '')]))

    return documents_pids


def load_articlemeta_journals_ids(collection, issns=None):
    rc = ThriftClient(domain=ARTICLEMETA_THRIFTSERVER, admintoken=ADMINTOKEN)

    journals_pids = []
    logger.info('Loading articlemeta journals ids')
    for issn in issns or [None]:
        for journal in rc.journals(collection, issn=issn, only_identifiers=True):
            logger.debug(
                'Loading articlemeta journal id (%s)',
                '_'.join([journal.collection, journal.code])
            )
            journals_pids.append('_'.join([journal.collection, journal.code]))

    return journals_pids


def run(collection, issns):

    rc = ThriftClient(domain=ARTICLEMETA_THRIFTSERVER, admintoken=ADMINTOKEN)

    logger.info('Running Isis2mongo')
    logger.debug('Thrift Server: %s', ARTICLEMETA_THRIFTSERVER)
    logger.debug('Admin Token: %s', ADMINTOKEN)
    logger.info('Loading data for collection: %s', collection)

    articlemeta_documents = set(
        load_articlemeta_documents_ids(collection, issns))
    articlemeta_issues = set(
        load_articlemeta_issues_ids(collection, issns))
    articlemeta_journals = set(
        load_articlemeta_journals_ids(collection, issns))

    with DataBroker(uuid.uuid4()) as ctrl:
        for coll, record in load_isis_records(collection, issns):
            ctrl.write_record(coll, record)

        legacy_documents = set(ctrl.articles_ids)
        legacy_issues = set(ctrl.issues_ids)
        legacy_journals = set(ctrl.journals_ids)

        new_documents = list(legacy_documents - articlemeta_documents)
        new_issues = list(legacy_issues - articlemeta_issues)
        new_journals = list(legacy_journals - articlemeta_journals)

        to_remove_documents = list(articlemeta_documents - legacy_documents)
        to_remove_issues = list(articlemeta_issues - legacy_issues)
        to_remove_journals = list(articlemeta_journals - legacy_journals)

        # Including and Updating Documents
        logger.info(
            'Documents being included into articlemeta (%d)',
            len(new_documents)
        )
        for ndx, item in enumerate(new_documents):
            ndx += 1
            item = item.split('_')
            try:
                document_meta = ctrl.load_document(item[0], item[1])
            except:
                logger.error(
                    'Fail to load document into Articlemeta (%s)',
                    '_'.join([item[0], item[1]])
                )
                continue

            if not document_meta:
                logger.error(
                    'Fail to load document into Articlemeta (%s)',
                    '_'.join([item[0], item[1]])
                )
                continue

            try:
                rc.add_document(json.dumps(document_meta))
            except ServerError:
                logger.error(
                    'Fail to load document into Articlemeta (%s)',
                    '_'.join([item[0], item[1]])
                )
                continue

            logger.debug(
                'Document (%d, %d) loaded into Articlemeta (%s)',
                ndx, len(new_documents),
                '_'.join([item[0], item[1]])
            )

        # Removing Documents
        logger.info(
            'Documents to be removed from articlemeta (%d)',
            len(to_remove_documents)
        )
        if not len(to_remove_documents) > 2000:
            for item in to_remove_documents:
                item = item.split('_')
                try:
                    rc.delete_document(item[1], item[0])
                except UnauthorizedAccess:
                    logger.warning('Unauthorized access to remove itens, check the ArticleMeta admin token')
        else:
            logger.info('To many documents to be removed, the remove task will be skipped')

        # Including and Updating Journals
        logger.info(
            'Journals being included into articlemeta (%d)',
            len(new_journals)
        )
        for ndx, item in enumerate(new_journals):
            ndx += 1
            item = item.split('_')
            try:
                journal_meta = ctrl.load_journal(item[0], item[1])
            except:
                logger.error(
                    'Fail to load journal into Articlemeta (%s)',
                    '_'.join([item[0], item[1]])
                )
                continue
            if not journal_meta:
                logger.error(
                    'Fail to load journal into Articlemeta (%s)',
                    '_'.join([item[0], item[1]])
                )
                continue

            try:
                rc.add_journal(json.dumps(journal_meta))
            except ServerError:
                logger.error(
                    'Fail to load document into Articlemeta (%s)',
                    '_'.join([item[0], item[1]])
                )
                continue

            logger.debug(
                'Journal (%d, %d) loaded into Articlemeta (%s)',
                ndx,
                len(new_journals),
                '_'.join([item[0], item[1]])
            )

        # Removing Journals
        logger.info(
            'Journals to be removed from articlemeta (%d)',
            len(to_remove_journals)
        )
        if not len(to_remove_journals) > 5:
            for index, item in enumerate(to_remove_journals):
                item = item.split('_')
                try:
                    rc.delete_journal(item[1], item[0])
                except UnauthorizedAccess:
                    logger.warning('Unauthorized access to remove itens, check the ArticleMeta admin token')
        else:
            logger.info('To many journals to be removed, the remove task will be skipped')

        # Including and Updating Issues
        logger.info(
            'Issues to being included into articlemeta (%d)',
            len(new_issues)
        )
        for ndx, item in enumerate(new_issues):
            ndx += 1
            item = item.split('_')

            try:
                issue_meta = ctrl.load_issue(item[0], item[1])
            except:
                logger.error(
                    'Fail to load issue into Articlemeta (%s)',
                    '_'.join([item[0], item[1]])
                )
                continue

            if not issue_meta:
                logger.error(
                    'Fail to load issue into Articlemeta (%s)',
                    '_'.join([item[0], item[1]])
                )
                continue

            try:
                rc.add_issue(json.dumps(issue_meta))
            except ServerError:
                logger.error(
                    'Fail to load document into Articlemeta (%s)',
                    '_'.join([item[0], item[1]])
                )
                continue

            logger.debug(
                'Issue (%d, %d) loaded into Articlemeta (%s)',
                ndx,
                len(new_issues),
                '_'.join([item[0], item[1]])
            )

        # Removing Issues
        logger.info(
            'Issues to be removed from articlemeta (%d)',
            len(to_remove_issues)
        )
        if not len(to_remove_issues) > 20:
            for item in to_remove_issues:
                item = item.split('_')
                try:
                    rc.delete_issue(item[1], item[0])
                except UnauthorizedAccess:
                    logger.warning('Unauthorized access to remove itens, check the ArticleMeta admin token')
        else:
            logger.info(
                'To many issues to be removed, the remove task will be skipped')

    logger.info('Process Isis2mongo Finished')


def main():
    parser = argparse.ArgumentParser(
        description='Dump accesses'
    )

    parser.add_argument(
        'issns',
        nargs='*',
        help='ISSN\'s separated by spaces'
    )

    parser.add_argument(
        '--collection',
        '-c',
        help='Collection Acronym'
    )

    parser.add_argument(
        '--output_file',
        '-r',
        help='File to receive the dumped data'
    )

    parser.add_argument(
        '--logging_file',
        '-o',
        help='Full path to the log file'
    )

    parser.add_argument(
        '--logging_level',
        '-l',
        default=LOGGING_LEVEL,
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
        help='Loggin level'
    )

    args = parser.parse_args()
    LOGGING['handlers']['console']['level'] = args.logging_level
    for lg, content in LOGGING['loggers'].items():
        content['level'] = args.logging_level
    logging.config.dictConfig(LOGGING)

    run(args.collection, args.issns)
