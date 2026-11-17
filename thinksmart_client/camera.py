import asyncio
import fcntl
import os
import struct
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp


MAX_IMAGE_BYTES = 5 * 1024 * 1024
LIBCAMERA_ROOT = Path('/opt/thinksmart-camera')


def libcamera_available():
    return (LIBCAMERA_ROOT / 'bin/cam').is_file()


async def capture_libcamera():
    environment = dict(os.environ)
    environment['LD_LIBRARY_PATH'] = str(LIBCAMERA_ROOT / 'lib')
    environment['LIBCAMERA_LOG_LEVELS'] = '*:ERROR'
    with tempfile.TemporaryDirectory(prefix='thinksmart-camera-', dir='/dev/shm') as folder:
        frame = Path(folder) / 'frame.ppm'
        process = await asyncio.create_subprocess_exec(
            str(LIBCAMERA_ROOT / 'bin/cam'), '-c', '1', '--capture=60',
            '--stream=role=viewfinder,width=1304,height=980,pixelformat=RGB888',
            '--orientation=rot0', '--file=' + str(frame), env=environment,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        try:
            await asyncio.wait_for(process.communicate(), timeout=10)
            if process.returncode or not frame.is_file():
                raise ValueError('内置摄像头采集失败，请检查驱动、镜头遮挡和设备占用情况')
        except asyncio.TimeoutError:
            raise ValueError('内置摄像头采集超时，请稍后重试') from None
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        return await capture(str(frame), from_file=True)


def capture_devices():
    devices = []
    for path in sorted(Path('/dev').glob('video[0-9]*')):
        descriptor = None
        try:
            descriptor = os.open(path, os.O_RDWR | os.O_NONBLOCK)
            capability = bytearray(104)
            fcntl.ioctl(descriptor, 0x80685600, capability)
            flags, device_flags = struct.unpack_from('II', capability, 84)
            flags = device_flags if flags & 0x80000000 else flags
            if flags & (0x1 | 0x1000) and not flags & (0x4000 | 0x8000):
                devices.append(str(path))
        except OSError:
            pass
        finally:
            if descriptor is not None:
                os.close(descriptor)
    return devices


async def capture(device, from_file=False):
    try:
        process = await asyncio.create_subprocess_exec(
            'ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
            *([] if from_file else ['-f', 'v4l2']), '-i', device, '-frames:v', '1',
            '-vf', ("hflip,vflip," if from_file else '') + "scale='min(1280,iw)':-2", '-q:v', '3',
            '-f', 'image2pipe', '-vcodec', 'mjpeg', 'pipe:1',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        raise ValueError('设备尚未安装摄像头采集组件 ffmpeg') from None
    try:
        image, _ = await asyncio.wait_for(process.communicate(), timeout=3 if from_file else 10)
        if process.returncode or not image.startswith(b'\xff\xd8'):
            raise ValueError('摄像头采集失败，请检查设备权限、镜头遮挡和摄像头占用情况')
        if len(image) > MAX_IMAGE_BYTES:
            raise ValueError('摄像头图片超过大小限制')
        return image
    except asyncio.TimeoutError:
        raise ValueError('摄像头采集超时，请检查设备连接') from None
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


class Camera:
    def __init__(self, config, publish):
        self.config = config
        self.publish = publish
        self.token = ''
        self.task = None

    def configure(self, capabilities):
        vision = capabilities.get('vision') if isinstance(capabilities, dict) else None
        token = vision.get('token') if isinstance(vision, dict) else None
        self.token = token if isinstance(token, str) else ''

    async def cancel(self):
        task = self.task
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def explain(self, question):
        if not isinstance(question, str) or not question.strip() or len(question) > 2000:
            raise ValueError('请说明希望摄像头查看或识别的内容')
        if self.task is not None:
            raise ValueError('摄像头正在处理上一条请求，请稍后再试')
        if not self.token:
            raise ValueError('视觉服务尚未初始化，请重新连接音箱')
        base = self.config['hub_http'].rstrip('/')
        parsed = urlsplit(base)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            raise ValueError('视觉服务器地址无效')
        self.task = asyncio.current_task()
        try:
            preferred = self.config.get('camera_device', '')
            self.publish('toast', {'text': '正在拍照', 'kind': 'info'})
            if not preferred and libcamera_available():
                image = await capture_libcamera()
            else:
                devices = await asyncio.to_thread(capture_devices)
                if preferred:
                    devices = [device for device in devices if device == preferred]
                if not devices:
                    raise ValueError('Ubuntu 尚未识别到可用摄像头，请检查摄像头驱动、连接和服务权限')
                image = await capture(devices[0])
            self.publish('toast', {'text': '正在识别画面', 'kind': 'info'})
            form = aiohttp.FormData()
            form.add_field('question', question.strip())
            form.add_field('image', image, filename='camera.jpg', content_type='image/jpeg')
            headers = {'Authorization': 'Bearer ' + self.token,
                       'Device-Id': self.config['device_id'], 'Client-Id': self.config['client_id']}
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                async with session.post(base + '/mcp/vision/explain', data=form, headers=headers,
                                        allow_redirects=False) as response:
                    if response.status == 401:
                        raise ValueError('视觉认证已过期，请重新连接音箱')
                    if response.status != 200:
                        raise ValueError('视觉服务暂时不可用，请稍后重试')
                    result = await response.json()
            if not isinstance(result, dict):
                raise ValueError('视觉服务返回格式不正确')
            if result.get('success') is not True:
                raise ValueError('视觉分析失败，请检查 Hub 的默认视觉模型配置')
            text = result.get('response')
            if not isinstance(text, str) or not text.strip():
                raise ValueError('视觉模型没有返回识别结果')
            return {'success': True, 'description': text[:12000]}
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise ValueError('视觉分析连接失败或超时，请稍后重试') from None
        finally:
            self.task = None
