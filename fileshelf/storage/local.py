import io
import os
import stat
import uuid
import errno
import shutil
import uuid

from werkzeug.utils import secure_filename

import fileshelf.url as url
import fileshelf.content as content


class LocalStorage:
    def __init__(self, storage_dir, data_dir):
        os.path.isdir(storage_dir) or self._not_found(storage_dir)

        os.path.exists(data_dir) or os.mkdir(data_dir)

        clipboard_dir = os.path.join(data_dir, 'clipboard')
        os.path.exists(clipboard_dir) or os.mkdir(clipboard_dir)

        trash_dir = os.path.join(data_dir, 'trash')
        os.path.exists(trash_dir) or os.mkdir(trash_dir)

        self.storage_dir = storage_dir
        self.data_dir = data_dir
        self.clipboard_dir = clipboard_dir
        self.trash_dir = trash_dir

    def _not_found(self, path):
        raise OSError(errno.ENOENT, os.strerror(errno.ENOENT), path)

    def _fullpath(self, *args):
        return os.path.join(self.storage_dir, *args)

    def _log(self, msg):
        print('## LocalStorage: ' + msg)

    def make_dir(self, path):
        path = self._fullpath(path)
        try:
            os.mkdir(path)
        except OSError as e:
            return e

    def list_dir(self, path):
        path = self._fullpath(path)
        return os.listdir(path)

    def move_from_fpath(self, fpath, path, name):
        """ moves a file from external `fpath` to internal `path`/`name` """
        if os.path.sep in name:
            return ValueError('filename `%s` contains os.sep' % name)
        try:
            dst = self._fullpath(path, name)
            # just a security precaution:
            if not dst.startswith(self._fullpath()):
                self._log('dst=%s, fullpath=%s' % (dst, self._fullpath()))
                return ValueError('does not start with self._fullpath()')

            self._log('mv %s -> %s' % (fpath, dst))
            shutil.move(fpath, dst)
        except (OSError, IOError) as e:
            return e

    def rename(self, oldpath, newpath):
        old = self._fullpath(oldpath)
        new = self._fullpath(newpath)
        try:
            self._log("mv %s %s" % (old, new))
            shutil.move(old, new)
        except OSError as e:
            return e

    def delete(self, path):
        path = self._fullpath(path)
        try:
            if os.path.isdir(path):
                os.rmdir(path)
            else:
                os.remove(path)
        except OSError as e:
            return e

    def file_info(self, path):
        dir_st = os.lstat(self.storage_dir)

        fpath = os.path.join(self.storage_dir, path)

        def entry(): return 0
        entry.path = path
        entry.fpath = fpath

        st = os.lstat(fpath)
        entry.size = st.st_size
        entry.is_dir = stat.S_ISDIR(st.st_mode)

        entry.mimetype = content.guess_mime(path)

        def is_audio():
            return entry.mimetype and entry.mimetype.startswith('audio/')

        def is_viewable():
            return entry.mimetype and entry.mimetype in ['application/pdf']

        entry.is_audio = is_audio
        entry.is_viewable = is_viewable

        entry.ctime = st.st_ctime

        entry.can_delete = bool(dir_st.st_mode & stat.S_IWUSR)
        entry.can_rename = entry.can_delete
        entry.can_read = os.access(fpath, os.R_OK)
        entry.can_write = os.access(fpath, os.W_OK)

        return entry

    def exists(self, path):
        path = self._fullpath(path)
        ret = os.path.exists(path)
        return ret

    def is_dir(self, path):
        return os.path.isdir(self._fullpath(path))

    def read_text(self, path):
        """ returns (text or None, exception or None) """
        path = self._fullpath(path)
        try:
            text = io.open(path, encoding='utf8').read()
            return text, None
        except (IOError, OSError, UnicodeDecodeError) as e:
            return None, e

    def write_text(self, path, data):
        path = self._fullpath(path)
        try:
            data = data.decode('utf-8') if hasattr(data, 'decode') else data
            with io.open(path, 'w', encoding='utf8') as f:
                f.write(data)
        except (IOError, OSError, UnicodeDecodeError) as e:
            return e

    def _clipboard_db(self):
        return os.path.join(self.data_dir, 'cb.txt')

    def clipboard_cut(self, path):
        src = os.path.join(self.storage_dir, path)
        self._log('cut(%s)' % src)

        name = str(uuid.uuid1())
        dst = os.path.join(self.clipboard_dir, name)

        cb_db = self._clipboard_db()
        # try:
        shutil.move(src, dst)
        with io.open(cb_db, mode='a', encoding='utf8') as f:
            s = u'%s %s\n' % (name, path)
            self._log('adding cut record: ' + s)
            f.write(s)
        # except Exception as e:
        #    return e

    def clipboard_copy(self, path):
        name = str(uuid.uuid1())
        dst = os.path.join(self.clipboard_dir, name)
        src = os.path.join(self.storage_dir, path)
        try:
            os.symlink(src, dst)
            with io.open(self._clipboard_db(), 'a', encoding='utf8') as f:
                f.write('%s %s\n' % (name, path))
        except Exception as e:
            return e

    def clipboard_list(self):
        cb_db = self._clipboard_db()
        if not os.path.exists(cb_db):
            return []

        ret = []
        lines = io.open(cb_db, encoding='utf8').readlines()
        for line in lines:
            line = line.strip()
            u = line[:36]
            path = line[37:]
            cut = os.path.join(self.clipboard_dir, u)
            ret.append({
                'path': path,
                'tmp': cut,
                'cut': not os.path.islink(cut)
            })
        return ret

    def clipboard_paste(self, path=None, dryrun=False):
        ''' if path is None, move everything back '''
        cb_db = self._clipboard_db()
        if not os.path.exists(cb_db):
            self._log('no ' + cb_db)
            return

        lines = io.open(cb_db, encoding='utf8').readlines()
        for line in lines:
            self._log('processing: ' + line)
            line = line.strip()
            u = line[:36]
            dst = line[37:]
            if path is not None:
                dst = os.path.basename(dst)
                dst = os.path.join(path, dst)
            try:
                tmp = os.path.join(self.clipboard_dir, u)
                dst = os.path.join(self.storage_dir, dst)
                self._log('processing %s -> %s' % (tmp, dst))
                if os.path.islink(tmp):
                    src = os.readlink(tmp)
                    if path is not None:
                        self._log('copy "%s" "%s"' % (src, dst))
                        if os.path.isdir(src):
                            self._log('copytree(%s, %s)' % (src, dst))
                            dryrun or shutil.copytree(src, dst)
                        else:
                            self._log('copy(%s, %s)' % (src, dst))
                            dryrun or shutil.copy(src, dst)
                    self._log('rm "%s"' % tmp)
                    dryrun or os.remove(tmp)
                else:
                    # TODO: check if dirname(dst) still exists
                    # TODO: do something if there is a file with that name
                    # if not os.path.exists(dst):
                    self._log('mv "%s" "%s"' % (tmp, dst))
                    dryrun or shutil.move(tmp, dst)
            except Exception as e:
                self._log('clear(%s), error: %s' % (dst, e))
        try:
            self._log('rm ' + cb_db)
            dryrun or os.remove(cb_db)
        except Exception as e:
            self._log("tried to remove %s, failed: %s" % (cb_db, e))

    def static_download(self, path, offload):
        entry = self.file_info(path)
        if entry.size < offload.minsize:
            return (None, None)

        fname = os.path.basename(path)
        tmp_id = str(uuid.uuid1())
        tmp_dir = os.path.join(offload.dir, tmp_id)
        tmp_url = url.join(offload.path, tmp_id, fname)
        fpath = self._fullpath(path)
        tmp_path = os.path.join(tmp_dir, fname)
        # self._log('static_download: ln %s %s' % (fpath, tmp_path))
        try:
            os.mkdir(tmp_dir)
            os.symlink(fpath, tmp_path)
            return (tmp_url, None)
        except (OSError, IOError) as e:
            return (None, e)

    def mktemp(self, fname=None):
        tmp_dir = os.path.join(self.data_dir, 'tmp')
        if not os.path.exists(tmp_dir):
            os.mkdir(tmp_dir)
        _id = str(uuid.uuid1())
        if fname:
            fname = _id + '.' + secure_filename(fname)
        else:
            fname = _id
        return os.path.join(tmp_dir, fname)
