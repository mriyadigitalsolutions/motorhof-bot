"""Дроби EXIF в выходном JPEG — с ровным знаменателем (willhaben отклонял FocalLength 2988413/524283)."""
from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from modules.photos import exif
from modules.photos.convert import load_variants, to_jpeg

from tests.photos.conftest import JPEG_NAMED_DNG_FIXTURE, needs_jpeg_named_dng


def _src_exif():
    ex = Image.Exif()
    sub = ex.get_ifd(exif.EXIF_IFD)
    sub[0x920A] = IFDRational(2988413, 524283)   # FocalLength из iPhone-MPO
    sub[0x829A] = IFDRational(1669, 2785623)     # ExposureTime
    sub[0x829D] = IFDRational(3, 2)              # FNumber уже ровный
    return ex


def test_clean_tidies_rationals():
    sub = exif.clean(_src_exif()).get_ifd(exif.EXIF_IFD)
    focal, exposure, fnumber = sub[0x920A], sub[0x829A], sub[0x829D]
    assert (focal.numerator, focal.denominator) == (57, 10)
    assert exposure.denominator <= exif.RATIONAL_MAX_DENOMINATOR and abs(float(exposure) - 1669 / 2785623) < 1e-6
    assert (fnumber.numerator, fnumber.denominator) == (3, 2)


@needs_jpeg_named_dng
def test_iphone_mpo_output_has_round_focal_length(tmp_path):
    out = tmp_path / "MH_1016_01.jpg"
    to_jpeg(JPEG_NAMED_DNG_FIXTURE, load_variants()["listing"], out)
    focal = Image.open(out).getexif().get_ifd(exif.EXIF_IFD)[0x920A]
    assert focal.denominator <= exif.RATIONAL_MAX_DENOMINATOR
    assert (focal.numerator, focal.denominator) == (57, 10)
