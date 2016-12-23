# -*- coding: utf-8 -*-
#
# Picard, the next-generation MusicBrainz tagger
# Copyright (C) 2006 Lukáš Lalinský
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.

import base64
import re
import mutagen.flac
import mutagen.ogg
import mutagen.oggflac
import mutagen.oggspeex
import mutagen.oggtheora
import mutagen.oggvorbis
try:
    from mutagen.oggopus import OggOpus
    with_opus = True
except ImportError:
    OggOpus = None
    with_opus = False
from picard import config, log
from picard.coverart.image import TagCoverArtImage, CoverArtImageError
from picard.file import File
from picard.formats.id3 import types_from_id3, image_type_as_id3_num
from picard.metadata import Metadata
from picard.util import encode_filename, sanitize_date


class VCommentFile(File):

    """Generic VComment-based file."""
    _File = None

    __translate = {
        "musicbrainz_trackid": "musicbrainz_recordingid",
        "musicbrainz_releasetrackid": "musicbrainz_trackid",
    }
    __rtranslate = dict([(v, k) for k, v in __translate.iteritems()])

    def _load(self, filename):
        log.debug("Loading file %r", filename)
        file = self._File(encode_filename(filename))
        file.tags = file.tags or {}
        metadata = Metadata()
        for origname, values in file.tags.items():
            for value in values:
                name = origname
                if name == "date" or name == "originaldate":
                    # YYYY-00-00 => YYYY
                    value = sanitize_date(value)
                elif name == 'performer' or name == 'comment':
                    # transform "performer=Joe Barr (Piano)" to "performer:Piano=Joe Barr"
                    name += ':'
                    if value.endswith(')'):
                        start = len(value) - 2
                        count = 1
                        while count > 0 and start > 0:
                            if value[start] == ')':
                                count += 1
                            elif value[start] == '(':
                                count -= 1
                            start -= 1
                        if start > 0:
                            name += value[start + 2:-1]
                            value = value[:start]
                elif name.startswith('rating'):
                    try:
                        name, email = name.split(':', 1)
                    except ValueError:
                        email = ''
                    if email != config.setting['rating_user_email']:
                        continue
                    name = '~rating'
                    value = unicode(int(round((float(value) * (config.setting['rating_steps'] - 1)))))
                elif name == "fingerprint" and value.startswith("MusicMagic Fingerprint"):
                    name = "musicip_fingerprint"
                    value = value[22:]
                elif name == "tracktotal":
                    if "totaltracks" in file.tags:
                        continue
                    name = "totaltracks"
                elif name == "disctotal":
                    if "totaldiscs" in file.tags:
                        continue
                    name = "totaldiscs"
                elif name == "metadata_block_picture":
                    image = mutagen.flac.Picture(base64.standard_b64decode(value))
                    try:
                        coverartimage = TagCoverArtImage(
                            file=filename,
                            tag=name,
                            types=types_from_id3(image.type),
                            comment=image.desc,
                            support_types=True,
                            data=image.data,
                        )
                    except CoverArtImageError as e:
                        log.error('Cannot load image from %r: %s' % (filename, e))
                    else:
                        metadata.append_image(coverartimage)

                    continue
                elif name in self.__translate:
                    name = self.__translate[name]
                metadata.add(name, value)
        if self._File == mutagen.flac.FLAC:
            for image in file.pictures:
                try:
                    coverartimage = TagCoverArtImage(
                        file=filename,
                        tag='FLAC/PICTURE',
                        types=types_from_id3(image.type),
                        comment=image.desc,
                        support_types=True,
                        data=image.data,
                    )
                except CoverArtImageError as e:
                    log.error('Cannot load image from %r: %s' % (filename, e))
                else:
                    metadata.append_image(coverartimage)

        # Read the unofficial COVERART tags, for backward compatibillity only
        if "metadata_block_picture" not in file.tags:
            try:
                for data in file["COVERART"]:
                    try:
                        coverartimage = TagCoverArtImage(
                            file=filename,
                            tag='COVERART',
                            data=base64.standard_b64decode(data)
                        )
                    except CoverArtImageError as e:
                        log.error('Cannot load image from %r: %s' % (filename, e))
                    else:
                        metadata.append_image(coverartimage)
            except KeyError:
                pass
        self._info(metadata, file)
        return metadata

    def _save(self, filename, metadata):
        """Save metadata to the file."""
        log.debug("Saving file %r", filename)
        is_flac = self._File == mutagen.flac.FLAC
        file = self._File(encode_filename(filename))
        if file.tags is None:
            file.add_tags()
        if config.setting["clear_existing_tags"]:
            file.tags.clear()
        if (is_flac and (config.setting["clear_existing_tags"] or
                         metadata.images_to_be_saved_to_tags)):
            file.clear_pictures()
        tags = {}
        for name, value in metadata.items():
            if name == '~rating':
                # Save rating according to http://code.google.com/p/quodlibet/wiki/Specs_VorbisComments
                if config.setting['rating_user_email']:
                    name = 'rating:%s' % config.setting['rating_user_email']
                else:
                    name = 'rating'
                value = unicode(float(value) / (config.setting['rating_steps'] - 1))
            # don't save private tags
            elif name.startswith("~"):
                continue
            elif name.startswith('lyrics:'):
                name = 'lyrics'
            elif name == "date" or name == "originaldate":
                # YYYY-00-00 => YYYY
                value = sanitize_date(value)
            elif name.startswith('performer:') or name.startswith('comment:'):
                # transform "performer:Piano=Joe Barr" to "performer=Joe Barr (Piano)"
                name, desc = name.split(':', 1)
                if desc:
                    value += ' (%s)' % desc
            elif name == "musicip_fingerprint":
                name = "fingerprint"
                value = "MusicMagic Fingerprint%s" % value
            elif name in self.__rtranslate:
                name = self.__rtranslate[name]
            tags.setdefault(name.upper().encode('utf-8'), []).append(value)

        if "totaltracks" in metadata:
            tags.setdefault(u"TRACKTOTAL", []).append(metadata["totaltracks"])
        if "totaldiscs" in metadata:
            tags.setdefault(u"DISCTOTAL", []).append(metadata["totaldiscs"])

        for image in metadata.images_to_be_saved_to_tags:
            picture = mutagen.flac.Picture()
            picture.data = image.data
            picture.mime = image.mimetype
            picture.desc = image.comment
            picture.type = image_type_as_id3_num(image.maintype)
            if self._File == mutagen.flac.FLAC:
                file.add_picture(picture)
            else:
                tags.setdefault(u"METADATA_BLOCK_PICTURE", []).append(
                    base64.standard_b64encode(picture.write()))

        file.tags.update(tags)

        for tag in metadata.deleted_tags:
            real_name = self._get_tag_name(tag)
            if real_name and real_name in file.tags:
                if real_name in ('performer', 'comment'):
                    tag_type = "\(%s\)" % tag.split(':', 1)[1]
                    for item in file.tags.get(real_name):
                        if re.search(tag_type, item):
                            file.tags.get(real_name).remove(item)
                else:
                    del file.tags[real_name]

        kwargs = {}
        if is_flac and config.setting["remove_id3_from_flac"]:
            kwargs["deleteid3"] = True
        try:
            file.save(**kwargs)
        except TypeError:
            file.save()

    def _get_tag_name(self, name):
        if name == '~rating':
            if config.setting['rating_user_email']:
                return 'rating:%s' % config.setting['rating_user_email']
            else:
                return 'rating'
        elif name.startswith("~"):
            return None
        elif name.startswith('lyrics:'):
            return 'lyrics'
        elif name.startswith('performer:') or name.startswith('comment:'):
            return name.split(':', 1)[0]
        elif name == 'musicip_fingerprint':
            return 'fingerprint'
        elif name in self.__rtranslate:
            return self.__rtranslate[name]
        else:
            return name


class FLACFile(VCommentFile):

    """FLAC file."""
    EXTENSIONS = [".flac"]
    NAME = "FLAC"
    _File = mutagen.flac.FLAC

    def _info(self, metadata, file):
        super(FLACFile, self)._info(metadata, file)
        metadata['~format'] = self.NAME


class OggFLACFile(VCommentFile):

    """FLAC file."""
    EXTENSIONS = [".oggflac"]
    NAME = "Ogg FLAC"
    _File = mutagen.oggflac.OggFLAC

    def _info(self, metadata, file):
        super(OggFLACFile, self)._info(metadata, file)
        metadata['~format'] = self.NAME


class OggSpeexFile(VCommentFile):

    """Ogg Speex file."""
    EXTENSIONS = [".spx"]
    NAME = "Speex"
    _File = mutagen.oggspeex.OggSpeex

    def _info(self, metadata, file):
        super(OggSpeexFile, self)._info(metadata, file)
        metadata['~format'] = self.NAME


class OggTheoraFile(VCommentFile):

    """Ogg Theora file."""
    EXTENSIONS = [".oggtheora"]
    NAME = "Ogg Theora"
    _File = mutagen.oggtheora.OggTheora

    def _info(self, metadata, file):
        super(OggTheoraFile, self)._info(metadata, file)
        metadata['~format'] = self.NAME


class OggVorbisFile(VCommentFile):

    """Ogg Vorbis file."""
    EXTENSIONS = [".ogg"]
    NAME = "Ogg Vorbis"
    _File = mutagen.oggvorbis.OggVorbis

    def _info(self, metadata, file):
        super(OggVorbisFile, self)._info(metadata, file)
        metadata['~format'] = self.NAME


class OggOpusFile(VCommentFile):

    """Ogg Opus file."""
    EXTENSIONS = [".opus"]
    NAME = "Ogg Opus"
    _File = OggOpus

    def _info(self, metadata, file):
        super(OggOpusFile, self)._info(metadata, file)
        metadata['~format'] = self.NAME


def _select_ogg_type(filename, options):
    """Select the best matching Ogg file type."""
    fileobj = file(filename, "rb")
    results = []
    try:
        header = fileobj.read(128)
        results = [
            (option._File.score(filename, fileobj, header), option.__name__, option)
            for option in options]
    finally:
        fileobj.close()
    results.sort()
    if not results or results[-1][0] <= 0:
        raise mutagen.ogg.error("unknown Ogg audio format")
    return results[-1][2](filename)


def OggAudioFile(filename):
    """Generic Ogg audio file."""
    options = [OggFLACFile, OggSpeexFile, OggVorbisFile]
    return _select_ogg_type(filename, options)


OggAudioFile.EXTENSIONS = [".oga"]
OggAudioFile.NAME = "Ogg Audio"


def OggVideoFile(filename):
    """Generic Ogg video file."""
    options = [OggTheoraFile]
    return _select_ogg_type(filename, options)


OggVideoFile.EXTENSIONS = [".ogv"]
OggVideoFile.NAME = "Ogg Video"
