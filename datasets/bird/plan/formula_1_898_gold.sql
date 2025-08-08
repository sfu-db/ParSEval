{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 4,
      "type": "BIGINT"
    }
  ],
  "dir": [
    "DESCENDING"
  ],
  "offset": 0,
  "limit": 1,
  "id": "6",
  "inputs": [
    {
      "relOp": "LogicalAggregate",
      "keys": [
        {
          "column": 0,
          "type": "VARCHAR"
        },
        {
          "column": 1,
          "type": "VARCHAR"
        },
        {
          "column": 2,
          "type": "VARCHAR"
        }
      ],
      "aggs": [
        {
          "operator": "AVG",
          "distinct": false,
          "ignoreNulls": false,
          "operands": [
            {
              "column": 3,
              "type": "FLOAT"
            }
          ],
          "type": "FLOAT",
          "name": "EXPR$3"
        },
        {
          "operator": "COUNT",
          "distinct": false,
          "ignoreNulls": false,
          "operands": [
            {
              "column": 4,
              "type": "INTEGER"
            }
          ],
          "type": "BIGINT",
          "name": "DESC"
        }
      ],
      "id": "5",
      "inputs": [
        {
          "relOp": "LogicalProject",
          "project": [
            {
              "kind": "INPUT_REF",
              "index": 4,
              "name": "$4",
              "type": "VARCHAR"
            },
            {
              "kind": "INPUT_REF",
              "index": 5,
              "name": "$5",
              "type": "VARCHAR"
            },
            {
              "kind": "INPUT_REF",
              "index": 7,
              "name": "$7",
              "type": "VARCHAR"
            },
            {
              "kind": "INPUT_REF",
              "index": 12,
              "name": "$12",
              "type": "FLOAT"
            },
            {
              "kind": "INPUT_REF",
              "index": 15,
              "name": "$15",
              "type": "INTEGER"
            }
          ],
          "id": "4",
          "inputs": [
            {
              "relOp": "LogicalFilter",
              "condition": {
                "kind": "EQUALS",
                "operator": "=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 15,
                    "name": "$15",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "LITERAL",
                    "value": 1,
                    "type": "INTEGER",
                    "nullable": false,
                    "precision": 10
                  }
                ]
              },
              "variableset": "[]",
              "id": "3",
              "inputs": [
                {
                  "relOp": "LogicalJoin",
                  "joinType": "inner",
                  "condition": {
                    "kind": "EQUALS",
                    "operator": "=",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "INPUT_REF",
                        "index": 11,
                        "name": "$11",
                        "type": "INTEGER"
                      },
                      {
                        "kind": "INPUT_REF",
                        "index": 0,
                        "name": "$0",
                        "type": "INTEGER"
                      }
                    ]
                  },
                  "id": "2",
                  "inputs": [
                    {
                      "relOp": "LogicalTableScan",
                      "table": "drivers",
                      "id": "0",
                      "inputs": []
                    },
                    {
                      "relOp": "LogicalTableScan",
                      "table": "driverStandings",
                      "id": "1",
                      "inputs": []
                    }
                  ]
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}