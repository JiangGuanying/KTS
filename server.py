# System Imports
import os
import time
import logging
import simplejson

# Flask Imports
from flask import Flask, request, session, render_template, \
    send_file, redirect, url_for, g
import werkzeug
from werkzeug import secure_filename

# Flask extensions
from flask.ext.login import LoginManager, login_user, logout_user, \
    login_required

# Utility Imports
import myKalturaObject
import properties
import utils
from filetypes import get_KalturaMediaType_from_file, \
    get_KalturaMediaType_from_pull_url, get_specified_type
from exceldumps import create_workbook, close_workbook, get_new_worksheet, \
    write_to_worksheet
from users import User


# note: kaltura exceptions provide a decent str method, so i'll
# change exception handlers and log the exception with str(e)

SETTINGS = {}
properties.load_server_settings(SETTINGS)

app = Flask(__name__)
# Customize max content length
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024 * 1024
# Configure For Upload Folder
app.config['UPLOAD_FOLDER'] = SETTINGS['UPLOAD_FOLDER']
# Default Logger for our Calls
utils.addFileLogger(app.logger, "kaltura_server.log", 2)
# Logger for Werkzerg / Flask Related Requests
request_logger = logging.getLogger("request_log")
utils.addFileLogger(request_logger, "request_log.log", 2)
werkzeug._internal._logger = request_logger

# set the secret key.  keep this really secret:
app.secret_key = 'A0Zr98j/3yX R~XHH!jmN]LWX/,?RT'

lm = LoginManager()
lm.login_view = 'login'
lm.refresh_view = 'login'
lm.init_app(app)


@lm.user_loader
def load_user(userid):
    if userid == 'admin':
        return User('admin')
    else:
        return None


def parse_ids():
    return _parse_ids(request.args, request.form)


def _parse_ids(args, form):
    kaltura_id = None
    entry_id = None
    if 'id' in args:
        if ":" in args['id']:
            kaltura_id, entry_id = args['id'].split(':')
        else:
            kaltura_id, entry_id = "1", args["id"]
    elif 'id' in form:
        if ":" in form['id']:
            kaltura_id, entry_id = form['id'].split(':')
        else:
            kaltura_id, entry_id = "1", form["id"]
    if not kaltura_id:
        kaltura_id = form.get('kaltura_id', None)
    if not kaltura_id:
        kaltura_id = args.get('kaltura_id', "1")
    if not entry_id:
        entry_id = args.get('entry_id', None)
    if not entry_id:
        entry_id = form.get('entry_id', None)
    if entry_id and ":" in entry_id:
        kaltura_id, entry_id = entry_id.split(":")
    return kaltura_id, entry_id


def kaltura_session_loader_base(kaltura_id, session):
    # Refresh Kaltura Session If not
    print ("Current in session" + repr(session))
    kkey = 'kaltura-{}'.format(kaltura_id)
    if kkey in session and 'ksusages' in session[kkey] \
        and 'ksessionkey' in session[kkey] \
        and 'kstime' in session[kkey] and session[kkey]['ksusages'] < 5 and \
           (time.time() - session[kkey]['kstime']) < myKalturaObject.KS_EXPIRY:
        print ("I already have session. kid: {} ks: {} usages: {}" . 
        format(
            kaltura_id, session[kkey]['ksessionkey'], session[kkey]['ksusages']))
        session[kkey]['ksusages'] += 1
        return myKalturaObject.create_session(kaltura_id,
                                              session[kkey]["ksessionkey"])
    else:
        session[kkey] = {}
        settings = properties.load_kaltura_settings().get(kaltura_id)
        session[kkey]["ksessionkey"] = myKalturaObject.get_new_session_key(
            settings)
        session[kkey]["ksusages"] = 0
        session[kkey]["kstime"] = time.time()
        return myKalturaObject.create_session(kaltura_id,
                                              session[kkey]["ksessionkey"])


def allowed_file(filename):
    return True


def kaltura_session_loader(kaltura_id):
    return kaltura_session_loader_base(kaltura_id, session)


@app.after_request
def after(response):
    response.headers.add('Access-Control-Allow-Headers',
                         'Content-Type, X-Requested-With')
    return response


@app.route('/login')
def login():
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


@app.route('/config_home')
@login_required
def kts_index():
    return render_template('kts_index.html')


@app.route('/auth', methods=["POST"])
def auth():
    user = request.form.get('kts_user')
    if request.form.get('kts_pass') == SETTINGS['KTS_ADMIN_PWD'] and user == \
            SETTINGS['KTS_ADMIN_USER']:
        login_user(User('admin'), remember=False)
        return redirect(request.args.get('next') or url_for('kts_index'))
    else:
        return "Invalid credentials, try again."


@app.route('/add_config', methods=['GET'])
@login_required
def add_config():
    return render_template('kaltura_config_form.html')


@app.route('/add_config/', methods=['GET'])
@login_required
def submit_add_config():
    values = [request.args.get(properties.kaltura_properties_list[j]) for \
              j in utils.rangegen(1, len(properties.kaltura_properties_list))]
    resp = simplejson.loads(properties.add_kaltura(values))
    if resp.get('success', False) == True:
        message = 'Added'
    else:
        message = 'Failed to add'
    # return render_template('message.html',
    #                         message=message,
    #                         link=url_for('kts_index'),
    #                         linktext='-Home-')
    return redirect(url_for('view_configs'))


@app.route('/del_config', methods=['GET'])
@login_required
def del_config():
    return render_template('kaltura_del_form.html')


@app.route('/del_config/', methods=['GET'])
@login_required
def submit_del_config():
    resp = simplejson.loads(
        properties.rem_kaltura(request.args.get('KALTURA_CONFIG_ID')))
    if resp.get('success', False) == True:
        message = 'Deleted'
    else:
        message = 'Failed to delete'
    # return render_template('message.html',
    #                         message=message,
    #                         link=url_for('kts_index'),
    #                         linktext='-Home-')
    return redirect(url_for('view_configs'))


@app.route('/update_config', methods=['GET'])
@login_required
def update_config():
    kaltura_id = request.args.get('KALTURA_CONFIG_ID')
    settings = properties.load_kaltura_settings().get(kaltura_id)
    return render_template('kaltura_update_config.html',
                           settings=settings,
                           kaltura_id=kaltura_id)


@app.route('/update_config/', methods=['GET'])
@login_required
def submit_update_config():
    proplist = properties.kaltura_properties_list
    values = [request.args.get(item) for item in proplist]
    kal_id = values.pop(0)
    resp = simplejson.loads(properties.update_kaltura(kal_id, values))
    if resp.get('success', False) == True:
        message = 'Update'
    else:
        message = 'Failed to update'
    # return render_template('message.html',
    #                         message=message,
    #                         link=url_for('kts_index'),
    #                         linktext='-Home-')
    return redirect(url_for('view_configs'))


@app.route('/view_configs/getjson')
@login_required
def view_configs_getjson():
    return simplejson.dumps(properties.load_kaltura_settings())


@app.route('/view_configs/prettyjson')
@login_required
def view_configs_prettyjson():
    prettified = simplejson.dumps(properties.load_kaltura_settings(),
                                  sort_keys=True, indent=4 * ' ')
    prettified = '<html><body><pre>' + prettified + '</pre></body></html>'
    return prettified


@app.route('/view_configs')
@app.route('/view_configs/')
@login_required
def view_configs():
    props = properties.load_kaltura_settings()
    columns = ['id'] + list(props[list(props.keys())[0]].keys())
    data = [[key] + list(props[key].values())
            for key in sorted(list(props.keys()))]
    return render_template('kaltura_configs.html',
                           columns=columns,
                           data=data)


@app.route('/service/kaltura_session_start', methods=['GET', 'POST'])
def kaltura_session_start():
    kaltura_id = request.form.get('kaltura_id', None)
    if not kaltura_id:
        kaltura_id = request.args.get('kaltura_id', "1")
    if not kaltura_id:
        raise Exception("kaltura_id not provided")
    return repr(kaltura_session_loader(kaltura_id, True))


@app.route('/service/search_video/', methods=['GET', 'POST'])
def search_service():
    kaltura_id, entry_id = parse_ids()
    client = kaltura_session_loader(kaltura_id)
    # composite = request.args.get('composite',None) or \
    #                           request.form.get('composite', None)
    entriesData = myKalturaObject.searchVideos(client, kaltura_id, True)
    return simplejson.dumps(entriesData)


@app.route('/service/get_excel/', methods=['GET', 'POST'])
def get_excel():
    kaltura_id, entry_id = parse_ids()
    fields = request.args.get('fields', None)
    if fields:
        fields = [item.lower() for item in fields.split(',') if
                  item.lower() in myKalturaObject.DEFAULT_SEARCH_FIELD_LIST]
    else:
        fields = myKalturaObject.DEFAULT_SEARCH_FIELD_LIST
    filename = request.args.get('filename', None)
    # if i don't set pagesize, kaltura uses 30 as default.
    # max is 500 btw.
    pagesize = int(request.args.get('pagesize', 500))
    types = request.args.get('types', 'video,audio').split(',')
    skip_types = [each for each in ['video', 'audio', 'image'] if
                  each not in types]
    skip_vals = {'media_type': [each.upper() for each in skip_types]}
    if not filename:
        # raise Exception('filename is required')
        settings = properties.load_kaltura_settings().get(kaltura_id)
        filename = str(kaltura_id) + settings['KALTURA_NAME']
    filename = secure_filename(filename)
    client = kaltura_session_loader(kaltura_id)
    totaling_success, total = myKalturaObject.count(client)
    workbook, filepath = create_workbook(filename)
    worksheet = get_new_worksheet(workbook)
    i = total / pagesize + (1 if total % pagesize else 0)
    skipped = 0
    for num in utils.rangegen(i):
        data = myKalturaObject.searchVideos(
            client, kaltura_id, True, int(pagesize), num + 1)
        write_result = write_to_worksheet(worksheet,
                                          data,
                                          fields,
                                          int(pagesize) * num - skipped,
                                          skip_vals)
        skipped += write_result['skipped']
    close_workbook(workbook)
    return send_file(filepath, as_attachment=True,
                     attachment_filename=os.path.basename(filepath))


@app.route('/service/upload_file', methods=['GET', 'POST'])
def upload_file_service():
    """Upload a file to kaltura (via remote URL or file POST).

    Returns a JSON object containing "success", "kaltura_id" (misnomer,
    deprecated), "messages" and "entry_id".
    """
    g.msgs = []
    kaltura_id = request.form.get('kaltura_id', None)
    if not kaltura_id:
        kaltura_id = request.args.get('kaltura_id', "1")
    if not kaltura_id:
        raise Exception("kaltura_id not provided")
        # kaltura_session = kaltura_session_loader(kaltura_id)
    if request.method == 'POST':
        print ("Inside Post Body")
        # Three Modes of Consumption.
        # 1. FromLocal
        # 2. File Post
        # 3. PullPath
        pull_path = request.form.get('pullPath', None)
        if not pull_path:
            pull_path = request.args.get('pullPath', None)
        print ("PullPath", pull_path)

        fromlocal = request.form.get('fromlocal', None)
        if not fromlocal:
            fromlocal = request.args.get('fromlocal', None)
        print ("FromLocal", fromlocal)

        print (request.files.keys())
        medianame = request.form.get('medianame', None)
        if not medianame:
            medianame = request.args['medianame']

        split_medianame = medianame.split(".")
        if len(split_medianame) >= 2:
            medianame = ".".join(split_medianame[:-1])
        # medianame = medianame.encode('ascii', errors='xmlcharrefreplace')
        inputfile = None
        if fromlocal:
            upload_path = fromlocal
            media_type = get_KalturaMediaType_from_file(upload_path)
            (upload_status, upload_info) = \
                myKalturaObject.uploadVideo(upload_path, medianame,
                                            client=kaltura_session_loader(
                                                kaltura_id),
                                            media_type=media_type)
            g.msgs.append("Uploaded to Kaltura")
            if upload_status:
                return simplejson.dumps({
                    'success': True,
                    'messages': g.msgs, 'kaltura_id': upload_info,
                    'entry_id': upload_info})
            else:
                return simplejson.dumps({
                    'success': False, 'messages': g.msgs,
                    'kaltura_id': upload_info, 'entry_id': upload_info})
        elif pull_path:
            # Must Instruct Kaltura to pull from this path
            # and also supply media name
            deftype = request.args.get('default', None)
            if not deftype:
                deftype = request.form.get('default', None)
            if not deftype:
                deftype = 'video'
            user_defined_type = request.form.get(
                'mediatype') or request.args.get('mediatype')
            if user_defined_type:
                media_type = get_specified_type(user_defined_type)
            else:
                media_type = get_KalturaMediaType_from_pull_url(pull_path,
                                                                deftype) \
                             or get_KalturaMediaType_from_pull_url(
                    medianame,
                    deftype)  # Applies where the url nor magic mime yield the correct type, will check media name just in case.
            (upload_status, upload_info) = \
                myKalturaObject.pullVideo(
                    pull_path, medianame,
                    client=kaltura_session_loader(kaltura_id),
                    media_type=media_type)
            g.msgs.append("Kaltura will now pull and process the video")
            if upload_status:
                return simplejson.dumps({
                    'success': True,
                    'messages': g.msgs, 'kaltura_id': upload_info,
                    'entry_id': upload_info})
            else:
                return simplejson.dumps({
                    'success': False, 'messages': g.msgs,
                    'kaltura_id': upload_info, 'entry_id': upload_info})
        else:
            inputfile = request.files['file']
            if inputfile and allowed_file(inputfile.filename):
                filename = secure_filename(inputfile.filename)
                upload_path = os.path.join(app.config['UPLOAD_FOLDER'],
                                           filename)
                inputfile.save(upload_path)
                media_type = get_KalturaMediaType_from_file(upload_path)
                g.msgs.append("File Uploaded locally")
                (upload_status, upload_info) = \
                    myKalturaObject.uploadVideo(
                        upload_path, medianame,
                        client=kaltura_session_loader(kaltura_id),
                        media_type=media_type)
                g.msgs.append("Uploaded to Kaltura")
                if upload_status:
                    return simplejson.dumps({
                        'success': True,
                        'messages': g.msgs,
                        'kaltura_id': upload_info, 'entry_id': upload_info})
                else:
                    return simplejson.dumps({
                        'success': False,
                        'messages': g.msgs,
                        'kaltura_id': upload_info, 'entry_id': upload_info})
    return simplejson.dumps({
        'success': False,
        'errorValue': 'Data not Posted', 'messages': g.msgs})


@app.route('/service/get_media/', methods=['GET', 'POST'])
def get_asset_data():
    kaltura_id, entry_id = parse_ids()
    width = request.args.get('width', 120)
    height = request.args.get('height', 120)
    client = kaltura_session_loader(kaltura_id)
    return simplejson.dumps(
        myKalturaObject.get_entry(entry_id, kaltura_id, client,
                                  width, height))


@app.route('/service/add_kts_mobile_flavor/', methods=['GET', 'POST'])
def add_mobile_flavor_to_kts():
    g.msgs = []
    kaltura_id, entry_id = parse_ids()
    settings = properties.load_kaltura_settings().get(kaltura_id)
    if settings['MOBILE_PLAYER_FLAVOR']:
        g.msgs.append('Flavor already configured.')
        return simplejson.dumps({'success': True,
                                 'messages': g.msgs})
    client = kaltura_session_loader(kaltura_id)
    flavor_id = myKalturaObject.add_kts_mobile_flavor(client, kaltura_id)
    # flavor_id = "123"
    g.msgs.append("Added flavor to kaltura")
    myKalturaObject.add_flavor_to_default_conversion_profile(client,
                                                             flavor_id,
                                                             kaltura_id)
    g.msgs.append("Added flavor to default conversion profile")
    proplist = properties.kaltura_properties_list
    values = [settings[item] for item in proplist if
              not item == 'KALTURA_CONFIG_ID']
    values[8] = flavor_id
    resp = simplejson.loads(properties.update_kaltura(kaltura_id, values))
    if resp['success']:
        g.msgs.append('Set flavor id as mobile player flavor in local '
                    'configurations for kaltura id %s' % kaltura_id)
    else:
        g.msgs.append('Failed to assign flavor id in local configuration.')
    return simplejson.dumps({
        "flavor_id": flavor_id,
        "success": True,
        "messages": g.msgs
    })


@app.route('/service/thumbnail_list/', methods=['GET', 'POST', 'OPTIONS'])
@utils.crossdomain(origin='*')
def get_thumbnail_list():
    kaltura_id, entry_id = parse_ids()
    if not entry_id:
        raise Exception('entry_id is required')
    client = kaltura_session_loader(kaltura_id)
    list_of_thumbs = myKalturaObject.thumbnail_list(client=client,
                                                    entry_id=entry_id)
    return simplejson.dumps(list_of_thumbs)


@app.route('/service/thumbnail_set_default/',
           methods=['GET', 'POST', 'OPTIONS'])
@utils.crossdomain(origin='*')
def set_thumbnail_default():
    g.msgs = []
    kaltura_id, entry_id = parse_ids()
    client = kaltura_session_loader(kaltura_id)
    thumbnail_id = request.args.get('thumbnail_id', None)
    if not thumbnail_id:
        thumbnail_id = request.form.get('thumbnail_id', None)
    if not thumbnail_id:
        g.msgs.append('thumbnail_id is required.')
        return simplejson.dumps({'success': False, 'messages': g.msgs})
    return simplejson.dumps(
        myKalturaObject.thumbnail_set_default(thumbnail_id=thumbnail_id,
                                              client=client))


@app.route('/service/remove_thumbnail/', methods=['GET', 'POST', 'OPTIONS'])
@utils.crossdomain(origin='*')
def remove_thumbnail():
    g.msgs = []
    kaltura_id, entry_id = parse_ids()
    client = kaltura_session_loader(kaltura_id)
    thumbnail_id = request.args.get('thumbnail_id', None)
    if not thumbnail_id:
        thumbnail_id = request.form.get('thumbnail_id', None)
    if not thumbnail_id:
        g.msgs.append('thumbnail_id is required.')
        return simplejson.dumps({'success': False, 'messages': g.msgs})
    return simplejson.dumps(
        myKalturaObject.thumbnail_delete(thumbnail_id=thumbnail_id,
                                         client=client))


@app.route('/service/update_thumbnail_file', methods=['GET', 'POST'])
def update_thumbnail():
    """Update thumbnail via file POST.

    Returns a JSON object containing "success", "kaltura_id" (misnomer,
    deprecated), "messages" and "thumbnail_id".
    """
    kaltura_id, entry_id = parse_ids()
    client = kaltura_session_loader(kaltura_id)
    g.msgs = []
    inputfile = None
    if entry_id is None:
        g.msgs.append("Entry Id is required")
    inputfile = request.files['file']
    if inputfile is None:
        g.msgs.append("Input File is required")
    if inputfile and allowed_file(inputfile.filename):
        filename = secure_filename(inputfile.filename)
        upload_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        inputfile.save(upload_path)
        set_default = request.args.get('default',
                                       False) or request.form.get('default',
                                                                  False)
        (upload_status, upload_info) = myKalturaObject.updateThumbnail(
            upload_path,
            entry_id, client,
            set_default=set_default)
        if upload_status:
            return simplejson.dumps({'success': True,
                                     'kaltura_id': upload_info,
                                     'messages': g.msgs,
                                     'thumbnail_id': upload_info})
        else:
            g.msgs.append("Internal Server Error")
            return simplejson.dumps({'success': False,
                                     'kaltura_id': upload_info,
                                     'messages': g.msgs,
                                     'thumbnail_id': upload_info})
    else:
        g.msgs.append("file type not allowed")
        return simplejson.dumps({'success': False, 'messages': g.msgs})


@app.route('/service/add_thumbnail_from_url/', methods=['GET', 'POST'])
def add_thumb_from_url():
    g.msgs = []
    kaltura_id, entry_id = parse_ids()
    client = kaltura_session_loader(kaltura_id)
    thumburl = request.args.get('url', None) or request.form.get('url')
    if not thumburl:
        raise Exception('must specify url to pull from '
                        '(add one to form data/url arguments)')
    addresponse = myKalturaObject.thumbnail_add_from_url(client=client,
                                                         entry_id=entry_id,
                                                         thumburl=thumburl)
    return simplejson.dumps(addresponse)
 
 
@app.route('/service/update_thumbnail_from_player', methods=['GET', 'POST'])
def update_thumbnail_from_player():
    kaltura_id, entry_id = parse_ids()
    client = kaltura_session_loader(kaltura_id)
    g.msgs = []
    offset = request.args.get('offset', None)
    if offset is None:
        g.msgs.append("Offset error")
    if entry_id is None:
        g.msgs.append("Entry Id error")
    (
    upload_status, upload_info) = myKalturaObject.updateThumbnailFromPlayer(
        float(offset),
        entry_id,
        client)
    if upload_status:
        return simplejson.dumps({'success': True,
                                 'thumbnail_id': upload_info,
                                 'messages': g.msgs})
    else:
        g.msgs.append("Internal Server Error")
        return simplejson.dumps({'success': False,
                                 'thumbnail_id': upload_info,
                                 'messages': g.msgs})


@app.route('/service/del_media/', methods=['GET', 'POST'])
def del_asset_data():
    kaltura_id, entry_id = parse_ids()
    return simplejson.dumps(myKalturaObject.del_entry(
        entry_id,
        client=kaltura_session_loader(kaltura_id)))


@app.route('/service/get_player/', methods=['GET', 'POST'])
def get_kaltura_player():
    kaltura_id, entry_id = parse_ids()
    settings = properties.load_kaltura_settings().get(kaltura_id)
    cache_timestamp = int(time.time())
    # kaltura_session_loader(session)
    # kclient = session.get("kclient", None)

    return render_template('kaltura_player.html',
                           kaltura_local=settings['KALTURA_PATH'],
                           partner_id=settings['PARTNER_ID'],
                           player_id=settings['PLAYER_ID'],
                           entry_id=entry_id, cache_timestamp=cache_timestamp)


@app.route('/service/get_thumbnail_player/', methods=['GET', 'POST'])
def get_kaltura_thumbnail_player():
    kaltura_id, entry_id = parse_ids()
    settings = properties.load_kaltura_settings().get(kaltura_id)
    cache_timestamp = int(time.time())
    success_callback = request.args.get('successCallback', None)
    success_callback_params = request.args.get('successCallbackParams',
                                               None)
    g.msgs = []
    callback = request.args.get('callback', None)
    callback = callback \
               and callback or \
               "/service/update_thumbnail_from_player?kaltura_id=" \
               + kaltura_id

    if entry_id is None:
        g.msgs.append("Entry Id error")
        return simplejson.dumps({'success': False, 'messages': g.msgs})
    # kaltura_session_loader(session)
    # kclient = session.get("kclient", None)
    return render_template('thumbnail_player.html',
                           kaltura_local=settings['KALTURA_PATH'],
                           partner_id=settings['PARTNER_ID'],
                           player_id=settings['THUMBNAIL_PLAYER_ID'],
                           entry_id=entry_id,
                           cache_timestamp=cache_timestamp,
                           callback=callback,
                           success_callback=success_callback,
                           success_callback_params=success_callback_params,
                           parent_host=request.args.get('parent_host',
                                                        None))


@app.route('/service/get_mobileplayer_url/', methods=['GET', 'POST'])
def get_kaltura_mobileplayer_url():
    kaltura_id, entry_id = parse_ids()
    cache_timestamp = int(time.time())
    # kaltura_session_loader(session)
    # kclient = session.get("kclient", None)
    settings = properties.load_kaltura_settings().get(kaltura_id)
    if not settings['MOBILE_PLAYER_FLAVOR']:
        return simplejson.dumps({"success": False,
                                 "messages": ['Flavor ID not configured!']})
    return render_template('kaltura_mobileplayer.html',
                           kaltura_local=settings['KALTURA_PATH'],
                           partner_id=settings['PARTNER_ID'],
                           player_id=settings['PLAYER_ID'],
                           entry_id=entry_id,
                           flavor_id=settings['MOBILE_PLAYER_FLAVOR'],
                           cache_timestamp=cache_timestamp)


@app.route('/', methods=['GET', 'POST'])
def upload_file_form():
    return '''
    <!doctype html>
    <title>Upload new File</title>
    <h1>Upload new File</h1>
    <form action="/service/upload_file"
        method=post enctype=multipart/form-data>
     <p>
        <input type=file name=file />
        medianame: <input name=medianame />
        kaltura_id: <input name="kaltura_id" />
        <input type=submit value=Upload />
     </p>
    </form>
    '''


@app.route('/thumnail_demo', methods=['GET', 'POST'])
def upload_thumbnail_form():
    return '''
    <!doctype html>
    <title>Upload new File</title>
    <h1>Upload new File</h1>
    <form action="/service/update_thumbnail_file"
        method=post enctype=multipart/form-data>
     <p>
        <input type=file name=file>
        Entry Id:<input name=entry_id>
        <input type=submit value=Upload>
    </form>
    '''


@app.route('/service/get_thumbnail/', methods=['GET', 'POST'])
def get_thumb():
    kaltura_id, entry_id = parse_ids()
    settings = properties.load_kaltura_settings().get(kaltura_id)
    return render_template("thumbframe.html",
                           partner_id=settings['PARTNER_ID'],
                           entry_id=entry_id,
                           kaltura_local=settings['KALTURA_PATH'])


@app.route('/service/get_thumbnail_url/', methods=['GET', 'POST'])
def get_thumb_url():
    """ you might want to remove the error checking from here later.
        (sends a GET for the image to check for error :D
        could also just attempt a get_media request.
        UPDATE: REMOVED THE ERROR CHECKING.)
        services should just use get_media and parse the json returned
        That json contains thumbnail url as well.
    """
    # width = request.args.get('width', None)
    # height = request.args.get('height', None)
    kaltura_id, entry_id = parse_ids()
    # settings = properties.load_kaltura_settings().get(kaltura_id)
    # if width and height:
    #     #response = urllib2.urlopen("http://" + settings['KALTURA_PATH']\
    #     #   + "/p/" + settings['PARTNER_ID'] + "/thumbnail/entry_id/" +\
    #     #   entry_id + "/width/" + width + "/height/" + height + "/")
    #     return simplejson.dumps({
    #         'success': True,
    #         'imageUrl': "http://" +
    #         settings['KALTURA_PATH'] +
    #         "/p/" + settings['PARTNER_ID'] +
    #         "/thumbnail/entry_id/" +
    #         entry_id + "/width/" +
    #         width + "/height/" + height + "/"})
    client = kaltura_session_loader(kaltura_id)
    default_thumb = myKalturaObject.thumbnail_get_default(client, entry_id)
    if not default_thumb is None:
        default_thumb['imageUrl'] = default_thumb['url']
        default_thumb['success'] = True
        return simplejson.dumps(default_thumb)
    else:
        settings = properties.load_kaltura_settings().get(kaltura_id)
        return simplejson.dumps({
            'success': True,
            'imageUrl': "http://" +
                        settings['KALTURA_PATH'] +
                        "/p/" + settings['PARTNER_ID'] +
                        "/thumbnail/entry_id/" +
                        entry_id + "/%s" % int(time.time())})
        # else:
        #     #response = urllib2.urlopen("http://" + settings['KALTURA_PATH']\
        #     #    + "/p/" + settings['PARTNER_ID'] + "/thumbnail/entry_id/" +\
        #     #     entry_id + "/")
        #     return simplejson.dumps({
        #         'success': True, 'imageUrl': "http://" +
        #         settings['KALTURA_PATH'] +
        #         "/p/" + settings['PARTNER_ID']
        #         + "/thumbnail/entry_id/" + entry_id + "/"})


@app.route('/service/add_caption/', methods=['GET', 'POST', 'OPTIONS'])
@utils.crossdomain(origin='*')
def add_caption_file():
    g.msgs = []
    filename = None
    kaltura_id, entry_id = parse_ids()
    client = kaltura_session_loader(kaltura_id)
    inputfile = request.files['file']
    if inputfile and allowed_file(inputfile.filename):
        filename = inputfile.filename.replace(os.path.sep, '_')
        upload_path = os.path.join(app.config['UPLOAD_FOLDER'],
                                   filename)
        inputfile.save(upload_path)
        g.msgs.append("File Uploaded locally")
    else:
        raise ValueError("File not provided or bad file name.")
    make_default = False
    if request.form.get('default', 'false').lower() is 'true' or \
                    request.args.get('default', 'false').lower() is 'true':
        make_default = True
    # there's an easier way to do a similar thing:
    language = request.form.get('language', None) or \
               request.args.get('language', None)
    name = request.form.get('name', None) or request.args.get('name', None)
    capformat = request.form.get('format', None) or \
                request.args.get('format', None)
    if capformat is None:
        fileext = os.path.splitext(filename)[1]
        if fileext == '.srt':
            capformat = 'srt'
        elif fileext in ['.xml', '.dfxp', '.ttml']:
            capformat = 'dfxp'
        else:
            g.msgs.append(
                'Subtitle file ext %s is not supported (not any of: .srt '
                '.xml .dfxp .ttml)' % (
                    fileext))
            return simplejson.dumps({'success': False, 'messages': g.msgs})
    else:
        capformat = capformat.lower()
        if not (capformat == 'srt' or capformat == 'dfxp'):
            g.msgs.append('format %s is not supported' % (capformat))
            return simplejson.dumps({'success': False, 'messages': g.msgs})

    caption_asset_id = myKalturaObject.add_caption(
        upload_path,
        entry_id=entry_id,
        client=client,
        language=language,
        default=make_default,
        label=name,
        capformat=capformat.lower()
    )
    g.msgs.append('caption added, id: ' + str(caption_asset_id))
    return simplejson.dumps({'success': True, 'messages': g.msgs,
                             'caption_id': str(caption_asset_id)})


@app.route('/service/remove_caption/', methods=['GET', 'POST', 'OPTIONS'])
@utils.crossdomain(origin='*')
def remove_caption():
    g.msgs = []
    kaltura_id, entry_id = parse_ids()
    client = kaltura_session_loader(kaltura_id)
    caption_id = request.args.get('caption_id', None)
    if not caption_id:
        caption_id = request.form.get('caption_id', None)
    if not caption_id:
        g.msgs.append('caption_id is required.')
        return simplejson.dumps({'success': False, 'messages': g.msgs})
    return simplejson.dumps(myKalturaObject.del_caption(caption_id, client))


@app.route('/service/set_caption_as_default/',
           methods=['GET', 'POST', 'OPTIONS'])
@utils.crossdomain(origin='*')
def caption_set_as_default():
    g.msgs = []
    kaltura_id, entry_id = parse_ids()
    client = kaltura_session_loader(kaltura_id)
    caption_id = request.args.get('caption_id', None)
    if not caption_id:
        caption_id = request.form.get('caption_id', None)
    if not caption_id:
        g.msgs.append('caption_id is required.')
        return simplejson.dumps({'success': False, 'messages': g.msgs})
    return simplejson.dumps(
        myKalturaObject.caption_set_default(caption_id, client))


@app.route('/service/list_captions/', methods=['GET', 'POST', 'OPTIONS'])
@utils.crossdomain(origin='*')
def caption_list_by_entry_id():
    kaltura_id, entry_id = parse_ids()
    if not entry_id:
        raise Exception('entry_id is required')
    client = kaltura_session_loader(kaltura_id)
    caption_list = myKalturaObject.get_entry_captions(entry_id, client)
    return simplejson.dumps(caption_list)


@app.route('/service/update_caption/', methods=['GET', 'POST', 'OPTIONS'])
@utils.crossdomain(origin='*')
def update_caption_details():
    kaltura_id, entry_id = parse_ids()
    client = kaltura_session_loader(kaltura_id)
    caption_id = request.args.get('caption_id', None) or \
                 request.form.get('caption_id', None)
    name = request.args.get('name', None) or request.form.get('name', None)
    default = request.args.get('default', None) or \
              request.form.get('default', None)
    language = request.args.get('language', None) or \
               request.form.get('language', None)
    capformat = request.args.get('format', None) or \
                request.form.get('format', None)
    return simplejson.dumps(
        myKalturaObject.update_caption(caption_id=caption_id,
                                       capformat=capformat,
                                       language=language,
                                       default=default,
                                       name=name,
                                       client=client))


if not SETTINGS['DEBUG_MODE']:
    @app.errorhandler(myKalturaObject.KalturaException)
    def handle_kaltura_server_error(error):
        app.logger.exception(str(error))
        error_dict = {
            "success": False,
            "messages": [str(error)] + g.get('msgs', [])
        }
        return simplejson.dumps(error_dict), 500

    @app.errorhandler(myKalturaObject.KalturaClientException)
    def handle_kaltura_client_error(error):
        app.logger.exception(str(error))
        error_dict = {
            "success": False,
            "messages": [str(error)] + g.get('msgs', [])
        }
        return simplejson.dumps(error_dict), 500

    @app.errorhandler(Exception)
    def handle_other_errors(error):
        app.logger.exception(str(error))
        error_dict = {
            "success": False,
            "messages": [str(error)] + g.get('msgs', [])
        }
        return simplejson.dumps(error_dict), 500


if __name__ == '__main__':
    app.debug = True if SETTINGS['DEBUG_MODE'] else False
    from pprint import pprint

    print ("Starting with server settings")
    pprint(SETTINGS)
    print ("Starting with kaltura settings")
    pprint(properties.load_kaltura_settings())
    app.run(host='0.0.0.0', port=int(SETTINGS['PORT']))
