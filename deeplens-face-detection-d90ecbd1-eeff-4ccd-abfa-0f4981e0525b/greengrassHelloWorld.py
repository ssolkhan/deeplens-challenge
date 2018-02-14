#
# Copyright Amazon AWS DeepLens, 2017
#

import os
import greengrasssdk
from threading import Timer
import time
import awscam
import cv2
import boto3
from threading import Thread
from botocore.session import Session

# Creating a greengrass core sdk client
client = greengrasssdk.client('iot-data')

# The information exchanged between IoT and clould has 
# a topic and a message body.
# This is the topic that this code uses to send messages to cloud
iotTopic = '$aws/things/{}/infer'.format(os.environ['AWS_IOT_THING_NAME'])

ret, frame = awscam.getLastFrame()
ret,jpeg = cv2.imencode('.jpg', frame) 
Write_To_FIFO = True
class FIFO_Thread(Thread):
    def __init__(self):
        ''' Constructor. '''
        Thread.__init__(self)
 
    def run(self):
        fifo_path = "/tmp/results.mjpeg"
        if not os.path.exists(fifo_path):
            os.mkfifo(fifo_path)
        f = open(fifo_path,'w')
        client.publish(topic=iotTopic, payload="Opened Pipe")
        while Write_To_FIFO:
            try:
                f.write(jpeg.tobytes())
            except IOError as e:
                continue  

def greengrass_infinite_infer_run():
    try:
        modelPath = "/opt/awscam/artifacts/mxnet_deploy_ssd_FP16_FUSED.xml"
        modelType = "ssd"
        input_width = 300
        input_height = 300
        prob_thresh = 0.25
        results_thread = FIFO_Thread()
        results_thread.start()

        # Send a starting message to IoT console
        client.publish(topic=iotTopic, payload="Face detection starts now")

        # Load model to GPU (use {"GPU": 0} for CPU)
        mcfg = {"GPU": 1}
        model = awscam.Model(modelPath, mcfg)
        client.publish(topic=iotTopic, payload="Model loaded")
        ret, frame = awscam.getLastFrame()
        if ret == False:
            raise Exception("Failed to get frame from the stream")
            
        yscale = float(frame.shape[0]/input_height)
        xscale = float(frame.shape[1]/input_width)

        ifAudioPlayed = False
        doInfer = True
        while doInfer:
            # Get a frame from the video stream
            ret, frame = awscam.getLastFrame()
            # Raise an exception if failing to get a frame
            if ret == False:
                raise Exception("Failed to get frame from the stream")

            # Resize frame to fit model input requirement
            frameResize = cv2.resize(frame, (input_width, input_height))

            # Run model inference on the resized frame
            inferOutput = model.doInference(frameResize)


            # Output inference result to the fifo file so it can be viewed with mplayer
            parsed_results = model.parseResult(modelType, inferOutput)['ssd']
            label = '{'
            ifFaceDetected = False
            for obj in parsed_results:
                if obj['prob'] < prob_thresh:
                    break
                ifFaceDetected = True
                xmin = int( xscale * obj['xmin'] ) + int((obj['xmin'] - input_width/2) + input_width/2)
                ymin = int( yscale * obj['ymin'] )
                xmax = int( xscale * obj['xmax'] ) + int((obj['xmax'] - input_width/2) + input_width/2)
                ymax = int( yscale * obj['ymax'] )
                cv2.rectangle(frame, (xmin, ymin), (xmax, ymax), (255, 165, 20), 4)
                label += '"{}": {:.2f},'.format(str(obj['label']), obj['prob'] )
                label_show = '{}: {:.2f}'.format(str(obj['label']), obj['prob'] )
                cv2.putText(frame, label_show, (xmin, ymin-15),cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 165, 20), 4)
            label += '"null": 0.0'
            label += '}'  
            client.publish(topic=iotTopic, payload = label)
            global jpeg
            ret,jpeg = cv2.imencode('.jpg', frame)
            
            if ifFaceDetected == True:
                print("identified faces to detect")
                if ifAudioPlayed == False:
                    # Upload to S3
                    image_url = write_image_to_s3(frame)
                    # play audio
                    play_audio()
                    ifAudioPlayed = True
            else:
                # recognize face and play audio only once until reset when no faces detected
                ifAudioPlayed = False
            
    except Exception as e:
        msg = "Test failed: " + str(e)
        client.publish(topic=iotTopic, payload=msg)
        print(msg)

    # Asynchronously schedule this function to be run again in 60 seconds
    Timer(60, greengrass_infinite_infer_run).start()

# Function to write to S3
# The function is creating an S3 client every time to use temporary credentials
# from the GG session over TES 
def write_image_to_s3(img):
    print("writing image to s3")
    session = Session()
    s3 = session.create_client('s3')
    file_name = 'DeepLens/image-'+time.strftime("%Y%m%d-%H%M%S")+'.jpg'
    # You can contorl the size and quality of the image
    encode_param=[int(cv2.IMWRITE_JPEG_QUALITY),90]
    _, jpg_data = cv2.imencode('.jpg', img, encode_param)
    response = s3.put_object(ACL='public-read', Body=jpg_data.tostring(),Bucket='rekog-face',Key=file_name)

    image_url = 'https://s3.amazonaws.com/<BUCKET_NAME>/'+file_name
    return image_url

# Function to play aduio
def play_audio():
    try:
        print("playing audio")
        tmpFileDir = '/tmp/'
        s3 = boto3.resource('s3')
        bucket = s3.Bucket('audio-rekog-face')
        # iterate through the audio files in the bucket, download and play them
        i = 1
        for audioFile in bucket.objects.all():
            audioFileKey = audioFile.key
            print(audioFileKey)
            audioFileName = tmpFileDir+str(i)+'.'+audioFileKey
            print(audioFileName)
            bucket.download_file(audioFileKey, audioFileName)
            os.system('mplayer ' + audioFileName)
            i+=1
        # clean up, remove audio files from the bucket
        bucket.objects.all().delete()
    except Exception as e:
        msg = "could not play audio " + str(e)
        print(msg)


# Execute the function above
greengrass_infinite_infer_run()


# This is a dummy handler and will not be invoked
# Instead the code above will be executed in an infinite loop for our example
def function_handler(event, context):
    return