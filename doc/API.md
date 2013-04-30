Resources
=========

`:owner` refers to a username. `:ds-slug` is a dataset slug. `:ss-name` is the
name of a submission set. `:p-id` and `:s-id` are the numeric ids of a place
and a submission, respectively.

  * **/api/v1/** -- List of all the dataset owners
  * **/api/v1/*:owner*/datasets/** -- List of a user's owned datasets
  * **/api/v1/*:owner*/datasets/*:ds-slug*/** -- A specific dataset instance
  * **/api/v1/*:owner*/datasets/*:ds-slug*/places/** -- List of places in a
    dataset
  * **/api/v1/*:owner*/datasets/*:ds-slug*/places/*:p-id*/** -- A specific
    place instance
  * **/api/v1/*:owner*/datasets/*:ds-slug*/submissions/** -- List of all
    submissions in a dataset
  * **/api/v1/*:owner*/datasets/*:ds-slug*/*:ss-name*/** -- List of all
    submissions belonging to a particular submission set in a dataset
  * **/api/v1/*:owner*/datasets/*:ds-slug*/places/*:p-id*/submissions/** --
    List of all submissions attached to a place
  * **/api/v1/*:owner*/datasets/*:ds-slug*/places/*:p-id*/*:ss-name*/** -- List
    of all submissions belonging to a particular submission set attached to a
    place
  * **/api/v1/*:owner*/datasets/*:ds-slug*/places/*:p-id*/*:ss-name*/*:s-id*/**
    -- A specific submission instance

Datasets
--------

The primary entry point into the API is a dataset. Each dataset has an owner,
and a user can own any number of datasets.

**Fields**:

  * *id*: the numeric id of the dataset; every dataset has a unique id
  * *slug*: the short name for the dataset, used in the url; no user can
    own two datasets with the same slug
  * *display_name*: the human-readable name for the dataset
  * *url*: the URL of the dataset
  * *owner*: an object with the `username` and `id` of the owner
  * *places*: an object with places metadata -- the number (`length`) of
    places, and the `url` of the place collection
  * *submissions*: a list of objects with meta data about each submission
    set -- `type` (the set name), `length`, and `url`
  * *keys*: an object that contains only the URL to the dataset's API keys

------------------------------------------------------------

### GET /api/v1/*:owner*/datasets/

Get a user's datasets

**Authentication**: Basic, session, or key auth *(optional)*

**Response Formats**: JSON (default), CSV, HTML, XML

**Sample URL**: http://api.shareabouts.org/api/v1/openplans/datasets/?format=json

**Sample Response**:

    [
      {
        "id": 31,
        "url": "http://api.shareabouts.org/api/v1/openplans/datasets/chicagobikes/",
        "display_name": "Chicago Bike Share exports",
        "slug": "chicagobikes",

        "keys": {
          "url": "http://api.shareabouts.org/api/v1/openplans/datasets/chicagobikes/keys/"
        },
        "owner": {
          "username": "openplans",
          "id": 7
        },
        "places": {
          "url": "http://api.shareabouts.org/api/v1/openplans/datasets/chicagobikes/places/",
          "length": 1281
        },
        "submissions": [
          {
            "url": "http://api.shareabouts.org/api/v1/openplans/datasets/chicagobikes/comments/",
            "length": 1166,
            "type": "comments"
          },
          {
            "url": "http://api.shareabouts.org/api/v1/openplans/datasets/chicagobikes/support/",
            "length": 12389,
            "type": "support"
          }
        ]
      },
      ...
    ]

------------------------------------------------------------

### POST /api/v1/*:owner*/datasets/

Create a user's datasets

**Authentication**: Basic or session auth *(required)*

**Content type**: application/json

**Sample URL**: http://api.shareabouts.org/api/v1/openplans/datasets/

**Sample Request Data**:

    {
      "slug": "mctesty",
      "display_name": "testy mctest"
    }

**Sample Response**:

    201 CREATED

    {
       "display_name": "testy mctest",
       "id": 90,
       "keys": {
           "url": "http://api.shareabouts.org/api/v1/openplans/datasets/mctesty/keys/"
       },
       "owner": {
           "id": 7,
           "username": "openplans"
       },
       "places": {
           "length": 0,
           "url": "http://api.shareabouts.org/api/v1/openplans/datasets/mctesty/places/"
       },
       "slug": "mctesty",
       "submissions": [],
       "url": "http://api.shareabouts.org/api/v1/openplans/datasets/mctesty/"
    }

------------------------------------------------------------

### PUT /api/v1/*:owner*/datasets/*:slug*/

Update a user's dataset

**Authentication**: Basic or session auth *(required)*

**Content type**: application/json

**Sample URL**: http://api.shareabouts.org/api/v1/openplans/datasets/mctesty/

**Sample Request Data**:

    {
      "slug": "mctesty",
      "display_name": "testy mctest"
    }

**Sample Response**:

    200 OK

    {
       "display_name": "testy mctest",
       "id": 90,
       "keys": {
           "url": "http://api.shareabouts.org/api/v1/openplans/datasets/mctesty/keys/"
       },
       "owner": {
           "id": 7,
           "username": "openplans"
       },
       "places": {
           "length": 0,
           "url": "http://api.shareabouts.org/api/v1/openplans/datasets/mctesty/places/"
       },
       "slug": "mctesty",
       "submissions": [],
       "url": "http://api.shareabouts.org/api/v1/openplans/datasets/mctesty/"
    }

------------------------------------------------------------

### DELETE /api/v1/*:owner*/datasets/*:slug*/

Delete a user's dataset

**Authentication**: Basic or session auth *(required)*

**Sample URL**: http://api.shareabouts.org/api/v1/openplans/datasets/mctesty/

**Sample Response**:

    204 NO CONTENT

------------------------------------------------------------

### GET /api/v1/*:owner*/datasets/*:slug*/

Get the details of a dataset

**Authentication**: Basic, session, or key auth *(optional)*

**Response Formats**: JSON (default), CSV, HTML, XML

**Sample URL**: http://api.shareabouts.org/api/v1/openplans/datasets/mctesty/

**Sample Response**:

      {
        "id": 31,
        "url": "http://api.shareabouts.org/api/v1/openplans/datasets/chicagobikes/",
        "display_name": "Chicago Bike Share exports",
        "slug": "chicagobikes",

        "keys": {
          "url": "http://api.shareabouts.org/api/v1/openplans/datasets/chicagobikes/keys/"
        },
        "owner": {
          "username": "openplans",
          "id": 7
        },
        "places": {
          "url": "http://api.shareabouts.org/api/v1/openplans/datasets/chicagobikes/places/",
          "length": 1281
        },
        "submissions": [
          {
            "url": "http://api.shareabouts.org/api/v1/openplans/datasets/chicagobikes/comments/",
            "length": 1166,
            "type": "comments"
          },
          {
            "url": "http://api.shareabouts.org/api/v1/openplans/datasets/chicagobikes/support/",
            "length": 12389,
            "type": "support"
          }
        ]
      }


Places
--------

Places are the basic unit of a dataset. They have a point geometry and attributes.

**Fields**:

* *attachments*:
* *created_datetime*:
* *dataset*:
* *id*:
* *location*:
* *submissions*:
* *updated_datetime*:
* *url*:
* *visible*:

------------------------------------------------------------

### GET /api/v1/*:owner*/datasets/*:slug*/places/

Get all places in a dataset

**Request Parameters**:

  * *include_invisible* *(only direct auth)*
  * *include_private_data* *(only direct auth)*
  * *include_submissions*

**Authentication**: Basic, session, or key auth *(optional)*

**Response Formats**: JSON (default), CSV, HTML, XML

**Sample URL**: http://api.shareabouts.org/api/v1/openplans/datasets/atm_surcharge/places/?format=json

**Sample Response**:

    [
        {
            "attachments": [],
            "created_datetime": "2013-02-15T13:23:36.754Z",
            "dataset": {
                "url": "http://api.shareabouts.org/api/v1/openplans/datasets/atm_surcharge/"
            },
            "id": 25519,
            "location": {
                "lat": 40.722347722199999,
                "lng": -73.997224330899996
            },
            "location_type": "ATM",
            "name": "",
            "submissions": [],
            "submitter_name": "",
            "surcharge": "",
            "updated_datetime": "2013-02-15T13:23:36.755Z",
            "url": "http://api.shareabouts.org/api/v1/openplans/datasets/atm_surcharge/places/25519/",
            "visible": true
        },
        ...
    ]

------------------------------------------------------------

### POST /api/v1/*:owner*/datasets/*:slug*/places/

Create a place for a dataset

**Authentication**: Basic or session auth *(required)*

**Content type**: application/json

**Sample URL**: http://api.shareabouts.org/api/v1/openplans/places/

**Sample Request Data**:

    {
      "description": "This is a great location.",
      "location": {"lat":40.72044500134832, "lng":-73.9999086856842},
      "location_type": "landmark",
      "name": "Location Name",
      "submitter_name": "Aaron",
      "visible": "true",
    }

**Sample Response**:

    201 CREATED

    {
        "location_type": "landmark",
        "attachments": [],
        "updated_datetime": "2013-04-29T22:20:58.010Z",
        "created_datetime": "2013-04-29T22:20:58.010Z",
        "description": "This is a great location.",
        "dataset": {
            "url": "http://shareaboutsapi-civicworks.dotcloud.com/api/v1/demo-user/datasets/demo-data/"
        },
        "visible": true,
        "location": {"lat": 40.7204450013, "lng": -73.999908685700007},
        "url": "http://shareaboutsapi-civicworks.dotcloud.com/api/v1/demo-user/datasets/demo-data/places/29664/",
        "submitter_name": "Aaron",
        "submissions": [],
        "id": 29664,
        "name": "Location Name"
    }

------------------------------------------------------------

### PUT /api/v1/*:owner*/datasets/*:slug*/places/*:place_id*/

Update a place for a dataset

**Authentication**: Basic or session auth *(required)*

**Content type**: application/json

**Sample URL**: http://api.shareabouts.org/api/v1/demo-user/datasets/demo-data/places/29664/

**Sample Request Data**:

    {
      "description": "This is a REALLY great location.",
      "location": {"lat":40.72044500134832, "lng":-73.9999086856842},
      "location_type": "landmark",
      "name": "Location Name",
      "submitter_name": "Frank",
      "visible": "true",
    }

**Sample Response**:

    200 OK

    {
        "location_type": "landmark",
        "attachments": [],
        "updated_datetime": "2013-04-29T22:20:58.010Z",
        "created_datetime": "2013-04-29T22:20:58.010Z",
        "description": "This is a REALLY great location.",
        "dataset": {
            "url": "http://shareaboutsapi-civicworks.dotcloud.com/api/v1/demo-user/datasets/demo-data/"
        },
        "visible": true,
        "location": {"lat": 40.7204450013, "lng": -73.999908685700007},
        "url": "http://shareaboutsapi-civicworks.dotcloud.com/api/v1/demo-user/datasets/demo-data/places/29664/",
        "submitter_name": "Frank",
        "submissions": [],
        "id": 29664,
        "name": "Location Name"
    }

------------------------------------------------------------

### DELETE /api/v1/*:owner*/datasets/*:slug*/places/*:place_id*/

Delete a place

**Authentication**: Basic or session auth *(required)*

**Sample URL**: http://api.shareabouts.org/api/v1/demo-user/datasets/demo-data/places/29664/

**Sample Response**:

    204 NO CONTENT

------------------------------------------------------------

### GET /api/v1/*:owner*/datasets/*:slug*/places/*:place_id*/

Get a place

**Request Parameters**:

  * *include_invisible* *(only direct auth)*
  * *include_private_data* *(only direct auth)*
  * *include_submissions*

**Authentication**: Basic, session, or key auth *(optional)*

**Response Formats**: JSON (default), CSV, HTML, XML

**Sample URL**: http://api.shareabouts.org/api/v1/demo-user/datasets/demo-data/places/29664/

**Sample Response**:

    200 OK

    {
        "location_type": "landmark",
        "attachments": [],
        "updated_datetime": "2013-04-29T22:20:58.010Z",
        "created_datetime": "2013-04-29T22:20:58.010Z",
        "description": "This is a REALLY great location.",
        "dataset": {
            "url": "http://shareaboutsapi-civicworks.dotcloud.com/api/v1/demo-user/datasets/demo-data/"
        },
        "visible": true,
        "location": {"lat": 40.7204450013, "lng": -73.999908685700007},
        "url": "http://shareaboutsapi-civicworks.dotcloud.com/api/v1/demo-user/datasets/demo-data/places/29664/",
        "submitter_name": "Frank",
        "submissions": [],
        "id": 29664,
        "name": "Location Name"
    }


Submissions
-----------

Submissions are stand-alone objects (key-value pairs) that can be attached to
a place. These could be comments, surveys responses, support/likes, etc. You
can attach multiple submission sets to a place.

**Fields**:

* *attachments*:
* *created_datetime*:
* *id*:
* *place*:
* *type*:
* *updated_datetime*:
* *url*:
* *visible*:

------------------------------------------------------------

### GET /api/v1/*:owner*/datasets/*:slug*/places/*:place_id*/*:submission_type*/

Get all submissions for a place

**Request Parameters**:

  * *include_invisible* *(only direct auth)*
  * *include_private_data* *(only direct auth)*

**Authentication**: Basic, session, or key auth *(optional)*

**Response Formats**: JSON (default), CSV, HTML, XML

**Sample URL**: http://api.shareabouts.org/api/v1/demo-user/datasets/demo-data/places/26836/comments/

**Sample Response**:

    200 OK

    [
        {
            "attachments": [],
            "comment": "Agreed.  Caught me a big one just a week ago.",
            "created_datetime": "2013-04-11T16:46:38.662Z",
            "id": 26902,
            "place": {
                "id": 26836,
                "url": "http://api.shareabouts.org/api/v1/demo-user/datasets/demo-data/places/26836/"
            },
            "submitter_name": "John",
            "type": "comments",
            "updated_datetime": "2013-04-11T16:46:38.662Z",
            "url": "http://api.shareabouts.org/api/v1/demo-user/datasets/demo-data/places/26836/comments/26902/",
            "visible": true
        },
        ...
    ]

------------------------------------------------------------

### POST /api/v1/*:owner*/datasets/*:slug*/places/*:place_id*/*:submission_type*/

Create a submission for a place

**Authentication**: Basic or session auth *(required)*

**Content type**: application/json

**Sample URL**: http://shareabouts-civicworks.dotcloud.com/api/places/29664/comments/

**Sample Request Data**:

    {
        comment: "This is great!"
        submitter_name: "Andy"
        visible: "on"
    }

**Sample Response**:

    201 CREATED

    {
        "comment": "This is great!",
        "attachments": [],
        "updated_datetime": "2013-04-30T15:38:54.449Z",
        "created_datetime": "2013-04-30T15:38:54.449Z",
        "visible": true,
        "place": {
            "url": "http://shareaboutsapi-civicworks.dotcloud.com/api/v1/demo-user/datasets/demo-data/places/29664/",
            "id": 29664
        },
        "url": "http://shareaboutsapi-civicworks.dotcloud.com/api/v1/demo-user/datasets/demo-data/places/29664/comments/29671/",
        "submitter_name": "Andy",
        "type": "comments",
        "id": 29671
    }

------------------------------------------------------------

### PUT /api/v1/*:owner*/datasets/*:slug*/places/*:place_id*/*:submission_type*/*:submission_id*/

Update a submission for a place of a specific type

**Authentication**: Basic or session auth *(required)*

**Content type**: application/json

**Sample URL**: http://shareabouts-civicworks.dotcloud.com/api/places/29664/comments/29671/

**Sample Request Data**:

    {
        comment: "This is REALLY great."
        submitter_name: "Andy"
        visible: "on"
    }

**Sample Response**:

    200 OK

    {
        "attachments": [],
        "comment": "This is REALLY great.",
        "created_datetime": "2013-04-30T15:38:54.449Z",
        "id": 29671,
        "place": {
            "id": 29664,
            "url": "http://shareaboutsapi-civicworks.dotcloud.com/api/v1/demo-user/datasets/demo-data/places/29664/"
        },
        "submitter_name": "Andy",
        "type": "comments",
        "updated_datetime": "2013-04-30T15:48:13.395Z",
        "url": "http://shareaboutsapi-civicworks.dotcloud.com/api/v1/demo-user/datasets/demo-data/places/29664/comments/29671/",
        "visible": true
    }

------------------------------------------------------------

### DELETE /api/v1/*:owner*/datasets/*:slug*/places/*:place_id*/*:submission_type*/*:submission_id*/

Delete a submission

**Authentication**: Basic or session auth *(required)*

**Sample URL**: http://shareabouts-civicworks.dotcloud.com/api/places/29664/comments/29671/

**Sample Response**:

    204 NO CONTENT

------------------------------------------------------------

### GET /api/v1/*:owner*/datasets/*:slug*/places/*:place_id*/*:submission_type*/*:submission_id*/

Get a submission for a place

**Request Parameters**:

  * *include_invisible* *(only direct auth)*
  * *include_private_data* *(only direct auth)*

**Authentication**: Basic, session, or key auth *(optional)*

**Response Formats**: JSON (default), CSV, HTML, XML

**Sample URL**: http://shareabouts-civicworks.dotcloud.com/api/places/29664/comments/29671/

**Sample Request Data**:

    {
        comment: "This is REALLY great."
        submitter_name: "Andy"
        visible: "on"
    }

**Sample Response**:

    200 OK

    {
        "attachments": [],
        "comment": "This is REALLY great.",
        "created_datetime": "2013-04-30T15:38:54.449Z",
        "id": 29671,
        "place": {
            "id": 29664,
            "url": "http://shareaboutsapi-civicworks.dotcloud.com/api/v1/demo-user/datasets/demo-data/places/29664/"
        },
        "submitter_name": "Andy",
        "type": "comments",
        "updated_datetime": "2013-04-30T15:48:13.395Z",
        "url": "http://shareaboutsapi-civicworks.dotcloud.com/api/v1/demo-user/datasets/demo-data/places/29664/comments/29671/",
        "visible": true
    }





Attachments
-----------

You can attach files to places and submissions.

  * **Method**: POST

    **URL**: /api/v1/&lt;owner&gt;/datasets/&lt;dataset&gt;/places/&lt;place&gt;/attachments/

    **Content type**: multipart/form-data

    **Fields**
      * *name*: The attachment's name -- should be unique within the place.
      * *file*: The attachment's file data.

    **Result**: A JSON object with the following fields:
      * *name*: The attachment's name
      * *url*: The URL of the attached file

For example, in Javascript (with jQuery), this can be done like:

    var data = new FormData();
    data.append('name', 'my-attachment')
    data.append('file', fileField.files[0])

    jQuery.ajax({
      url: '...',
      type: 'POST',
      data: data,

      contentType: false,
      processData: false
    });

Or, in Python, with requests:

    import requests
    requests.request(
        'POST',
        '...',
        data={'name': 'my-attachment'}
        files={'file': open('filename.jpg')}
    )