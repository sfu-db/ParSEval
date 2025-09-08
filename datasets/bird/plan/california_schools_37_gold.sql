{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 6,
      "type": "INTEGER"
    }
  ],
  "dir": [
    "DESCENDING"
  ],
  "offset": 0,
  "limit": 1,
  "id": "4",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 50,
          "name": "$50",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 51,
          "name": "$51",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 53,
          "name": "$53",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 54,
          "name": "$54",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 56,
          "name": "$56",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 57,
          "name": "$57",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 10,
          "name": "$10",
          "type": "INTEGER"
        }
      ],
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
                "index": 0,
                "name": "$0",
                "type": "VARCHAR"
              },
              {
                "kind": "INPUT_REF",
                "index": 11,
                "name": "$11",
                "type": "VARCHAR"
              }
            ]
          },
          "id": "2",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "satscores",
              "id": "0",
              "inputs": []
            },
            {
              "relOp": "LogicalTableScan",
              "table": "schools",
              "id": "1",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}