CREATE TABLE IF NOT EXISTS "badges" ("Id" INT, "UserId" INT, "Name" VARCHAR, "Date" TIMESTAMP, PRIMARY KEY ("Id"));

CREATE TABLE IF NOT EXISTS "comments" ("Id" INT, "PostId" INT, "Score" INT, "Text" VARCHAR, "CreationDate" TIMESTAMP, "UserId" INT, "UserDisplayName" VARCHAR, PRIMARY KEY ("Id"));

CREATE TABLE IF NOT EXISTS "postHistory" ("Id" INT, "PostHistoryTypeId" INT, "PostId" INT, "RevisionGUID" VARCHAR, "CreationDate" TIMESTAMP, "UserId" INT, "Text" VARCHAR, "Comment" VARCHAR, "UserDisplayName" VARCHAR, PRIMARY KEY ("Id"));

CREATE TABLE IF NOT EXISTS "postLinks" ("Id" INT, "CreationDate" TIMESTAMP, "PostId" INT, "RelatedPostId" INT, "LinkTypeId" INT, PRIMARY KEY ("Id"));

CREATE TABLE IF NOT EXISTS "posts" ("Id" INT, "PostTypeId" INT, "AcceptedAnswerId" INT, "CreaionDate" TIMESTAMP, "Score" INT, "ViewCount" INT, "Body" VARCHAR, "OwnerUserId" INT, "LasActivityDate" TIMESTAMP, "Title" VARCHAR, "Tags" VARCHAR, "AnswerCount" INT, "CommentCount" INT, "FavoriteCount" INT, "LastEditorUserId" INT, "LastEditDate" TIMESTAMP, "CommunityOwnedDate" TIMESTAMP, "ParentId" INT, "ClosedDate" TIMESTAMP, "OwnerDisplayName" VARCHAR, "LastEditorDisplayName" VARCHAR, PRIMARY KEY ("Id"));

CREATE TABLE IF NOT EXISTS "tags" ("Id" INT, "TagName" VARCHAR, "Count" INT, "ExcerptPostId" INT, "WikiPostId" INT, PRIMARY KEY ("Id"));

CREATE TABLE IF NOT EXISTS "users" ("Id" INT, "Reputation" INT, "CreationDate" TIMESTAMP, "DisplayName" VARCHAR, "LastAccessDate" TIMESTAMP, "WebsiteUrl" VARCHAR, "Location" VARCHAR, "AboutMe" VARCHAR, "Views" INT, "UpVotes" INT, "DownVotes" INT, "AccountId" INT, "Age" INT, "ProfileImageUrl" VARCHAR, PRIMARY KEY ("Id"));

CREATE TABLE IF NOT EXISTS "votes" ("Id" INT, "PostId" INT, "VoteTypeId" INT, "CreationDate" DATE, "UserId" INT, "BountyAmount" INT, PRIMARY KEY ("Id"));

SELECT CAST(COUNT(T1.Id) AS FLOAT) * 100 / (SELECT COUNT(Id) FROM users) FROM users AS T1 INNER JOIN badges AS T2 ON T1.Id = T2.UserId WHERE T2.Name = 'Teacher';

SELECT CAST(COUNT(T2.UserId) AS FLOAT) * 100 / COUNT(T1.UserId) FROM users AS T1 LEFT JOIN badges AS T2 ON T1.Id = T2.UserId WHERE T2.Name = 'Teacher'