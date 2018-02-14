from __future__ import print_function

import boto3
from decimal import Decimal
import json
import urllib
print('Loading function')

rekognition = boto3.client('rekognition')
facesBucket = boto3.resource('s3').Bucket('aionedge-master-faces')
s3Resource = boto3.resource('s3')
s3Client = boto3.client('s3')
pollyClient = boto3.client('polly')


# --------------- Helper Functions to call Rekognition APIs ------------------


def detect_faces(bucket, key):
    response = rekognition.detect_faces(Image={"S3Object": {"Bucket": bucket, "Name": key}})
    return response


def detect_labels(bucket, key):
    response = rekognition.detect_labels(Image={"S3Object": {"Bucket": bucket, "Name": key}})

    # Sample code to write response to DynamoDB table 'MyTable' with 'PK' as Primary Key.
    # Note: role used for executing this Lambda function should have write access to the table.
    #table = boto3.resource('dynamodb').Table('MyTable')
    #labels = [{'Confidence': Decimal(str(label_prediction['Confidence'])), 'Name': label_prediction['Name']} for label_prediction in response['Labels']]
    #table.put_item(Item={'PK': key, 'Labels': labels})
    return response

def identify_faces(bucket, key, bucket_target, key_target, threshold=80):
    print("bucket = " + bucket)
    print("key = " + key)
    print("bucket_target = " + bucket_target)
    print("key_target = " + key_target)
    response = rekognition.compare_faces(
	    SourceImage={
			"S3Object": {
				"Bucket": bucket,
				"Name": key,
			}
		},
		TargetImage={
			"S3Object": {
				"Bucket": bucket_target,
				"Name": key_target,
			}
		},
	    SimilarityThreshold=threshold,
	)
    
    # here we can parse the response for faceMatches with the name of the target
    # and a confidence value of some percentage (%95)
    # then we can move the information about that person into a new seperate bucket
    
    if len(response['FaceMatches']) > 0:
        print("Found a Match!")
        
        #infoFileName = key + '.' + "Information.txt"
        index  = key.split(".")
        infoFileName = index[0] + "Information.txt"
        
        print("infoFileName = " + infoFileName)
            
        # download information file containing firstName, lastName, and biography
        # store file in /tmp/informationFile.txt
        s3Client.download_file(bucket, infoFileName, '/tmp/informationFile.txt')
        
        # open file that was just downloaded and parse it 
        with open('/tmp/informationFile.txt') as fileobj:
            firstName = fileobj.readline().strip() + '.'
            lastName = fileobj.readline().strip()
            biography = fileobj.readline().strip()
            print(firstName)
            print(lastName)
            print(biography)
    
        # use AWS Polly to turn text to speech (mp3 file)
        print("begin synthesizing speech with aws polly")
        pollyResponse = pollyClient.synthesize_speech(
            OutputFormat = 'mp3',
            SampleRate = '16000',
            Text = firstName + lastName + biography,
            VoiceId = 'Brian',
        )
        
        print("Polly Response = ")
        print(pollyResponse)
    
        mp3Key = firstName + lastName + ".mp3"
    
    
        # check to see if there is already an audio file 
        # if there is, delete it
        bucketList = s3Client.list_objects(Bucket='audio-rekog-face')
        print('bucketList = ')
        print(bucketList)
        
            
        #if len(bucketList) > 0:
        #    for key in bucketList['Contents']:
        #        print("key")
        #        print(key)
        #        s3Client.delete_object(Bucket='audio-rekog-face', Key=key['Key'])
        #print("made it here")
        
        s3Response = s3Client.put_object(
            Bucket = 'audio-rekog-face',
            Key = mp3Key,
            ACL = 'public-read',
            Body = pollyResponse['AudioStream'].read(),
            ContentType = 'audio/mpeg'
        )
        
        print("s3 put Object Response = ")
        print(s3Response)

    
    return response['FaceMatches']

def index_faces(bucket, key):
    # Note: Collection has to be created upfront. Use CreateCollection API to create a collecion.
    #rekognition.create_collection(CollectionId='BLUEPRINT_COLLECTION')
    response = rekognition.index_faces(Image={"S3Object": {"Bucket": bucket, "Name": key}}, CollectionId="BLUEPRINT_COLLECTION")
    return response


# --------------- Main handler ------------------


def lambda_handler(event, context):
    '''Demonstrates S3 trigger that uses
    Rekognition APIs to detect faces, labels and index faces in S3 Object.
    '''
    print(event)
    print("Received event: " + json.dumps(event, indent=2))

    # Get the object from the event
    bucket = event['Records'][0]['s3']['bucket']['name']
    key = urllib.unquote_plus(event['Records'][0]['s3']['object']['key'].encode('utf8'))
    try:
        # Calls rekognition DetectFaces API to detect faces in S3 object
        response = detect_faces(bucket, key)

        # Calls rekognition DetectLabels API to detect labels in S3 object
        response = detect_labels(bucket, key)

        # Calls rekognition to identify faces
        global facesBucket
        facesBucketName = 'aionedge-master-faces'
        #facesToMatch = facesBucket.list_objects(Bucket='aionedge-faces')['Contents']
        facesToMatch = facesBucket.meta.client.list_objects(Bucket=facesBucketName)
        print("facesToMatch = ")
        print(facesToMatch)
        for face in facesToMatch['Contents']:
            print(face)
            suffix = ".txt"
            if face['Key'].endswith(suffix):
            #if face['Key'] != 'Brady/tom_brady.jpeg':
            #if not face['Key'].endswith('.jpeg'):
                print("found a text file")
                continue
            if face['Key'].endswith("/"):
                print("found a folder")
                continue
            matches = identify_faces(facesBucketName, face['Key'], bucket, key)
            for match in matches:
                print("Target Face ({Confidence}%)".format(**match['Face']))
                print("  Similarity : {}%".format(match['Similarity']))
        
        # Calls rekognition IndexFaces API to detect faces in S3 object and index faces into specified collection
        #response = index_faces(bucket, key)

        return response
    except Exception as e:
        print(e)
        print("Error processing object {} from bucket {}. ".format(key, bucket) +
              "Make sure your object and bucket exist and your bucket is in the same region as this function.")
        raise e